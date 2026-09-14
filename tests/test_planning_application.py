import asyncio
from datetime import date

import pytest

from backend.application.planning import (
    PlanningApplicationService,
    PlanningConflictError,
    PlanningTaskRegistry,
)
from backend.models.requirement import TravelRequirement
from backend.models.travel_plan import TravelPlan, TravelPlanResponse


def _response() -> TravelPlanResponse:
    requirement = TravelRequirement(
        province="浙江省",
        cities=["杭州市"],
        days=1,
        interests=["自然"],
        travel_scope="single_city",
        start_date=date(2026, 9, 14),
    )
    plan = TravelPlan.model_construct()
    return TravelPlanResponse(requirement=requirement, plan=plan)


def test_planning_task_registry_prevents_duplicate_jobs_and_cleans_up():
    async def scenario():
        registry = PlanningTaskRegistry()
        started = asyncio.Event()
        release = asyncio.Event()

        async def work():
            started.set()
            await release.wait()

        task = registry.start("conversation-1", work)
        await started.wait()
        assert registry.is_running("conversation-1") is True
        with pytest.raises(PlanningConflictError):
            registry.start("conversation-1", work)

        release.set()
        await task
        assert registry.is_running("conversation-1") is False

    asyncio.run(scenario())


def test_dispatch_job_uses_external_queue_when_available():
    class Queue:
        enabled = True

        async def enqueue(self, job_id):
            assert job_id == "job-1"
            return True

    class Alerts:
        calls = []

        async def notify(self, **kwargs):
            self.calls.append(kwargs)

    service = PlanningApplicationService(
        settings=object(),
        queue=Queue(),
        alert_sink=Alerts(),
    )
    service._ensure_worker = lambda job_id: (_ for _ in ()).throw(
        AssertionError("external queue should not start a local worker")
    )

    assert asyncio.run(service.dispatch_job("job-1")) is True


def test_dispatch_job_falls_back_to_local_worker_on_queue_failure():
    class Queue:
        enabled = True

        async def enqueue(self, job_id):
            return False

    class Alerts:
        calls = []

        async def notify(self, **kwargs):
            self.calls.append(kwargs)

    alerts = Alerts()
    service = PlanningApplicationService(
        settings=object(),
        queue=Queue(),
        alert_sink=alerts,
    )
    started = []
    service._ensure_worker = lambda job_id: started.append(job_id)

    assert asyncio.run(service.dispatch_job("job-1")) is False
    assert started == ["job-1"]
    assert alerts.calls[0]["event"] == "planning_queue_unavailable"


def test_planning_stream_keeps_event_sequence_and_sends_keepalive(monkeypatch):
    import backend.application.planning as planning_module

    class ActiveRegistry:
        def is_running(self, job_id):
            return True

    async def scenario():
        service = PlanningApplicationService(settings=object(), task_registry=ActiveRegistry())
        reads = iter(
            [
                ("running", []),
                ("completed", [(4, "plan_completed", {"ok": True})]),
            ]
        )
        monkeypatch.setattr(service, "_read_events", lambda job_id, *, after: next(reads))
        monkeypatch.setattr(planning_module, "PLANNING_KEEPALIVE_SECONDS", 0.0)
        monkeypatch.setattr(planning_module, "PLANNING_POLL_SECONDS", 0.01)

        stream = service.stream_job_events("job-1")
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()

        assert first == (None, "heartbeat", {"status": "running"})
        assert second == (4, "plan_completed", {"ok": True})

    asyncio.run(scenario())


def test_standalone_planning_persists_conversation_and_plan(monkeypatch):
    import backend.application.planning as planning_module

    class FakeSession:
        def __init__(self):
            self.commit_count = 0
            self.rollback_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def commit(self):
            self.commit_count += 1

        def rollback(self):
            self.rollback_count += 1

    class FakeRow:
        id = "conversation-1"
        title = "新的旅行计划"
        status = "draft"
        requirement_draft = {}
        requirement_confirmed = False

    class FakeConversationRepository:
        row = FakeRow()

        def __init__(self, session):
            self.session = session
            self.messages = []
            self.saved_requirements = []

        def create(self):
            return self.row

        def add_message(self, row, *, role, content):
            self.messages.append((role, content))

        def set_status(self, row, status, *, commit=True):
            row.status = status
            if commit:
                self.session.commit()

        def save_requirement(self, row, requirement, *, confirmed=None, commit=True):
            self.saved_requirements.append((requirement, confirmed, commit))
            row.requirement_draft = requirement.model_dump(mode="json")
            if confirmed is not None:
                row.requirement_confirmed = confirmed
            if commit:
                self.session.commit()

        def get(self, conversation_id, *, for_update=False):
            return self.row if conversation_id == self.row.id else None

    class FakeJobRow:
        id = "job-1"
        conversation_id = "conversation-1"
        requirement_json = {}
        status = "queued"

    class FakeJobRepository:
        row = FakeJobRow()

        def __init__(self, session):
            self.session = session
            self.events = []

        def create(self, conversation_id, requirement):
            self.row.conversation_id = conversation_id
            self.row.requirement_json = requirement.model_dump(mode="json")
            self.row.status = "queued"
            return self.row

        def mark_running(self, row):
            row.status = "running"

        def append_event(self, row, event_type, payload):
            self.events.append((event_type, payload))

        def set_requirement(self, row, requirement):
            row.requirement_json = requirement.model_dump(mode="json")

        def mark_completed(self, row):
            row.status = "completed"

        def mark_waiting_for_choice(self, row):
            row.status = "waiting_for_choice"

        def mark_failed(self, row, message):
            row.status = "failed"

        def get(self, job_id, *, for_update=False):
            return self.row if job_id == self.row.id else None

    class FakePlanRepository:
        saved = []

        def __init__(self, session):
            self.session = session

        def save(self, session_id, result, *, commit=True):
            self.saved.append((session_id, result, commit))

    class FakeWorkflow:
        async def run(self, text, *, travel_scope=None):
            return _response()

    session = FakeSession()
    monkeypatch.setattr(planning_module, "open_session", lambda: session)
    monkeypatch.setattr(planning_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(planning_module, "PlanningJobRepository", FakeJobRepository)
    monkeypatch.setattr(planning_module, "TravelPlanRepository", FakePlanRepository)
    monkeypatch.setattr(planning_module, "build_travel_workflow", lambda session, settings: FakeWorkflow())

    execution = asyncio.run(
        PlanningApplicationService(settings=object()).run_standalone(
            "杭州玩1天，喜欢自然",
            travel_scope="single_city",
        )
    )

    assert execution.conversation_id == "conversation-1"
    assert FakeConversationRepository.row.status == "completed"
    assert FakeConversationRepository.row.requirement_confirmed is True
    assert FakePlanRepository.saved[0][0] == "conversation-1"
    assert FakePlanRepository.saved[0][2] is False
    # One commit publishes the running marker and the final commit publishes
    # the plan plus completed status together.
    assert session.commit_count == 2
