from fastapi.testclient import TestClient
from types import SimpleNamespace

from backend.attractions import get_attraction_repository
from backend.config import Settings, get_settings
from backend.destination.repository import get_catalog_repository
from backend.main import create_app


app = create_app(initialize_database=False)


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "milestone": "Final M11"}


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
    app.dependency_overrides[get_attraction_repository] = lambda: repository
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
