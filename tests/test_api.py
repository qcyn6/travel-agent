from datetime import datetime
from fastapi.testclient import TestClient
from types import SimpleNamespace

from backend.attractions import get_attraction_repository
from backend.api.dependencies import get_planning_application_service
from backend.api.conversations import _sse
from backend.config import Settings, get_settings
from backend.conversation import get_conversation_repository, get_general_conversation_repository
from backend.database import get_db
from backend.destination.repository import get_catalog_repository
from backend.knowledge import KnowledgeBootstrapError, get_knowledge_repository
from backend.main import create_app
from backend.models.attraction import AttractionPOI
from backend.models.conversation import RequirementDraft


app = create_app(initialize_database=False)


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "milestone": "Final M12"}


def test_metrics_endpoint_exposes_prometheus_text():
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE travel_agent_http_requests_total counter" in response.text


def test_list_conversations():
    repository = SimpleNamespace(
        list_recent=lambda *, limit: [
            SimpleNamespace(
                id="conversation-1",
                title="云南省旅行计划",
                status="draft",
                updated_at=datetime(2026, 9, 13, 14, 0),
            )
        ]
    )
    app.dependency_overrides[get_conversation_repository] = lambda: repository
    with TestClient(app) as client:
        response = client.get("/api/v1/conversations?limit=10")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["title"] == "云南省旅行计划"


def test_list_general_conversations_marks_records_as_general():
    repository = SimpleNamespace(
        list_recent=lambda *, limit: [
            SimpleNamespace(
                id="general-1",
                title="介绍云南",
                status="active",
                updated_at=datetime(2026, 9, 13, 15, 0),
            )
        ]
    )
    app.dependency_overrides[get_general_conversation_repository] = lambda: repository
    with TestClient(app) as client:
        response = client.get("/api/v1/general-conversations?limit=10")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["mode"] == "general"
    assert response.json()[0]["status"] == "active"


def test_general_agent_reply_is_streamed_through_the_api(monkeypatch):
    from backend.api import general_conversations as general_api

    class Service:
        async def prepare_message(self, conversation_id, message):
            assert conversation_id == "general-1"
            assert message == "从杭州东站到西湖怎么走"
            return SimpleNamespace(
                suggestions=[],
                user_message=message,
                destination_context=None,
                recent_messages=[],
                memory_summary=None,
            )

        @staticmethod
        def responder_inputs(prepared):
            return {
                "requirement": RequirementDraft(),
                "missing_fields": [],
                "destination_context": prepared.destination_context,
                "recent_messages": prepared.recent_messages,
                "conversation_summary": prepared.memory_summary,
                "changed_fields": [],
                "conversation_mode": "general",
            }

        def finish_message(self, prepared, reply, *, card=None):
            assert reply == "我来帮你查一下这条路线。"
            assert card is None

    class Responder:
        async def stream(self, **kwargs):
            assert kwargs["user_message"] == "从杭州东站到西湖怎么走"
            assert kwargs["conversation_mode"] == "general"
            yield "我来帮你查一下这条路线。"

    app.dependency_overrides[get_general_conversation_repository] = lambda: object()
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(general_api, "_general_service", lambda *args: Service())
    monkeypatch.setattr(general_api, "_general_responder", lambda settings: Responder())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/general-conversations/general-1/messages",
            json={"message": "从杭州东站到西湖怎么走"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "event: message_delta" in response.text
    assert "我来帮你查一下这条路线" in response.text
    assert 'event: message_done' in response.text


def test_planning_endpoint_rejects_general_mode():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/conversations/not-a-planning-session/messages",
            json={"message": "介绍云南", "mode": "general"},
        )

    assert response.status_code == 409
    assert "general-conversations" in response.json()["detail"]


def test_planning_sse_uses_ai_guidance_reply(monkeypatch):
    """Planning turns emit the guarded AI guidance selected by the service."""

    from backend.api import conversations as conversations_api

    class Service:
        async def prepare_message(self, conversation_id, message, *, mode):
            assert conversation_id == "planning-1"
            assert message == "自然风光"
            assert mode == "planning"
            return SimpleNamespace(
                intent="planning",
                mode_boundary=None,
                validation_error=None,
                destination_error=None,
                should_invite_more=False,
                should_summarize=False,
                requirement=RequirementDraft(),
                missing=["preferences"],
                destination_context=None,
                user_message=message,
                recent_messages=[],
                changed_fields=[],
                was_ready=False,
            )

        def suggestions_for(self, missing, requirement, *, intent):
            assert missing == ["preferences"]
            assert intent == "planning"
            return ["自然风光"]

        def suggestions_for_reply(self, prepared, reply):
            assert "更偏爱" in reply
            return ["自然风光"]

        async def planning_reply(self, prepared):
            assert prepared.missing == ["preferences"]
            return "为了更懂你，这次更偏爱自然、古城、摄影，还是其他体验？"

        @staticmethod
        def finish_message(prepared, reply):
            assert "更偏爱" in reply
            return SimpleNamespace(requirement_ready=False)

    app.dependency_overrides[get_conversation_repository] = lambda: object()
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(conversations_api, "_conversation_service", lambda *args: Service())
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/conversations/planning-1/messages",
                json={"message": "自然风光", "mode": "planning"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "为了更懂你" in response.text
    assert "回复连接中断" not in response.text


def test_delete_conversation():
    repository = SimpleNamespace(
        delete=lambda conversation_id: conversation_id == "conversation-1"
    )
    app.dependency_overrides[get_conversation_repository] = lambda: repository
    with TestClient(app) as client:
        response = client.delete("/api/v1/conversations/conversation-1")
        missing = client.delete("/api/v1/conversations/missing")
    app.dependency_overrides.clear()

    assert response.status_code == 204
    assert missing.status_code == 404


def test_standalone_error_exposes_conversation_id():
    class FailingPlanningService:
        async def run_standalone(self, text, *, travel_scope=None):
            error = KnowledgeBootstrapError("知识准备失败")
            error.conversation_id = "conversation-failed"
            raise error

    app.dependency_overrides[get_planning_application_service] = lambda: FailingPlanningService()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/travel/plan",
            json={"text": "去台北玩一天", "travel_scope": "single_city"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.headers["x-conversation-id"] == "conversation-failed"


def test_parse_and_resolve_api(catalog_repository):
    app.dependency_overrides[get_catalog_repository] = lambda: catalog_repository
    app.dependency_overrides[get_settings] = lambda: Settings(llm_api_key=None)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requirements/resolve",
            json={"text": "杭州和绍兴玩四天，喜欢历史文化和古镇"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["requirement"]["province"] == "浙江省"
    assert body["requirement"]["cities"] == ["杭州市", "绍兴市"]
    assert body["requirement"]["days"] == 4


def test_get_attraction_knowledge_status():
    attraction = SimpleNamespace(
        poi_id="A1",
        name="西湖风景名胜区",
        knowledge_status="ready",
        knowledge_error=None,
    )
    repository = SimpleNamespace(find_by_poi_id=lambda poi_id: attraction if poi_id == "A1" else None)
    knowledge_repository = SimpleNamespace(
        find=lambda poi_id: object() if poi_id == "A1" else None
    )
    app.dependency_overrides[get_attraction_repository] = lambda: repository
    app.dependency_overrides[get_knowledge_repository] = lambda: knowledge_repository
    with TestClient(app) as client:
        response = client.get("/api/v1/attractions/A1/knowledge/status")
        missing = client.get("/api/v1/attractions/missing/knowledge/status")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "poi_id": "A1",
        "name": "西湖风景名胜区",
        "knowledge_status": "ready",
        "knowledge_error": None,
    }
    assert missing.status_code == 404


def test_attraction_endpoint_filters_local_pois_then_enriches_with_mcp(monkeypatch):
    city = SimpleNamespace(id=1, name="杭州市")
    catalog = SimpleNamespace(find_city=lambda name: city if name in {"杭州", "杭州市"} else None)
    stored = []

    class AttractionRepository:
        def __init__(self):
            self.rows = [
                AttractionPOI(
                    poi_id="LOCAL-1",
                    name="西湖",
                    city="杭州市",
                    category="自然山水",
                    tags=["自然"],
                    description="本地景点描述",
                    visit_duration_minutes=120,
                )
            ]

        def list_for_city(self, city_id, *, interests=None, statuses=None, limit=30):
            del city_id, interests, statuses
            return [
                SimpleNamespace(
                    poi_id=item.poi_id,
                    name=item.name,
                    category=item.category,
                    tags=item.tags,
                    description=item.description,
                    visit_duration_minutes=item.visit_duration_minutes,
                    longitude=item.longitude,
                    latitude=item.latitude,
                )
                for item in self.rows[:limit]
            ]

        def upsert_many(self, city_id, attractions):
            stored.append((city_id, attractions))
            return len(attractions)

    class FakeMcpClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def enrich_attractions(self, attractions):
            assert [item.poi_id for item in attractions] == ["LOCAL-1"]
            return [
                attractions[0].model_copy(
                    update={"longitude": 120.148, "latitude": 30.243}
                )
            ]

    monkeypatch.setattr("backend.api.attractions.AmapMcpAttractionClient", FakeMcpClient)
    app.dependency_overrides[get_catalog_repository] = lambda: catalog
    app.dependency_overrides[get_attraction_repository] = AttractionRepository
    app.dependency_overrides[get_settings] = lambda: Settings(amap_api_key="test-key")
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/cities/杭州市/attractions?limit=1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["poi_id"] == "LOCAL-1"
    assert response.json()[0]["longitude"] == 120.148
    assert stored[0][0] == 1


def test_planning_sse_exposes_job_id_and_event_cursor():
    assert _sse("planning_progress", {"step": "destination"}, event_id=4) == (
        'id: 4\nevent: planning_progress\ndata: {"step":"destination"}\n\n'
    )

    class FakePlanningService:
        def prepare_conversation(self, conversation_id):
            return SimpleNamespace(job_id="job-new")

        def stream_job_events(self, job_id, *, after):
            assert job_id == "job-new"
            assert after == 0

            async def events():
                yield 1, "job_queued", {"status": "queued"}
                yield 2, "plan_completed", {"ok": True}

            return events()

    app.dependency_overrides[get_planning_application_service] = lambda: FakePlanningService()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/conversations/conversation-1/plan?after=7&job_id=job-old"
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["x-planning-job-id"] == "job-new"
    assert "id: 1\nevent: job_queued" in response.text
    assert "id: 2\nevent: plan_completed" in response.text
