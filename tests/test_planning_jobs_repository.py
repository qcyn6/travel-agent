from contextlib import contextmanager
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import backend.database.tables  # noqa: F401 - register all ORM tables
from backend.application.planning import PlanningApplicationService
from backend.conversation.repository import ConversationRepository
from backend.database.session import Base
from backend.models.conversation import RequirementDraft
from backend.planning_jobs.repository import PlanningJobRepository


def test_planning_job_events_are_replayable_in_sequence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        conversation = ConversationRepository(session).create()
        repository = PlanningJobRepository(session)
        job = repository.create(conversation.id, RequirementDraft())
        repository.append_event(job, "job_queued", {"status": "queued"})
        session.commit()

        repository.mark_running(job)
        repository.append_event(job, "job_started", {"status": "running"})
        session.commit()

        replayed = repository.list_events(job.id)
        assert [(item.sequence_number, item.event_type) for item in replayed] == [
            (1, "job_queued"),
            (2, "job_started"),
        ]
        assert repository.list_events(job.id, after=1)[0].event_type == "job_started"
        assert repository.active_for_conversation(conversation.id).id == job.id


def test_planning_job_lease_rejects_fresh_competitor_and_reclaims_stale_worker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        conversation = ConversationRepository(session).create()
        repository = PlanningJobRepository(session)
        job = repository.create(conversation.id, RequirementDraft())
        session.flush()

        assert repository.claim(
            job,
            worker_id="worker-a",
            stale_after_seconds=30,
        ) is True
        session.commit()

        assert repository.claim(
            job,
            worker_id="worker-b",
            stale_after_seconds=30,
        ) is False
        assert repository.owns(job, "worker-a") is True

        job.heartbeat_at = datetime.now() - timedelta(seconds=31)
        assert repository.claim(
            job,
            worker_id="worker-b",
            stale_after_seconds=30,
        ) is True
        assert repository.owns(job, "worker-b") is True
        assert repository.owns(job, "worker-a") is False
        assert repository.record_progress(
            job,
            step="old-worker",
            label="should be ignored",
            status="running",
            worker_id="worker-a",
        ) is None
        assert repository.record_progress(
            job,
            step="new-worker",
            label="accepted",
            status="running",
            worker_id="worker-b",
        ) is not None


def test_prepare_conversation_reuses_the_active_job(monkeypatch):
    import backend.application.planning as planning_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        conversation = ConversationRepository(session).create()
        requirement = RequirementDraft(
                province="浙江省",
                days=1,
                interests=["自然"],
                preference_text="节奏轻松",
                travel_scope="single_city",
                city_preference_confirmed=True,
                start_date=date.today() + timedelta(days=1),
            daily_start_time="08:00",
            daily_end_time="21:00",
            transport_mode="transit",
        )
        row = ConversationRepository(session).get(conversation.id)
        ConversationRepository(session).save_requirement(row, requirement, confirmed=True)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(planning_module, "open_session", sessions)
    service = PlanningApplicationService(settings=object())

    first = service.prepare_conversation(conversation.id)
    second = service.prepare_conversation(conversation.id)

    assert second.job_id == first.job_id
    with Session(engine) as session:
        job = PlanningJobRepository(session).get(first.job_id)
        assert job.status == "queued"
        assert [event.event_type for event in job.events] == ["job_queued"]


def test_prepare_conversation_resumes_the_checkpointed_choice_job(monkeypatch):
    import backend.application.planning as planning_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        conversation = ConversationRepository(session).create()
        original = RequirementDraft(
            province="浙江省",
            days=2,
            interests=["自然"],
            preference_text="节奏轻松",
            travel_scope="auto",
            province_preference_confirmed=True,
            city_preference_confirmed=True,
            start_date=date.today() + timedelta(days=1),
            daily_start_time="08:00",
            daily_end_time="21:00",
            transport_mode="auto",
        )
        selected = original.model_copy(update={"travel_scope": "single_city"})
        conversation = ConversationRepository(session).get(conversation.id)
        ConversationRepository(session).save_requirement(
            conversation,
            selected,
            confirmed=True,
        )
        repository = PlanningJobRepository(session)
        job = repository.create(conversation.id, original)
        repository.mark_waiting_for_choice(job)
        repository.append_event(
            job,
            "destination_choice_required",
            {"options": {"requires_choice": True}},
        )
        session.commit()

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(planning_module, "open_session", sessions)
    service = PlanningApplicationService(settings=object())

    resumed = service.prepare_conversation(conversation.id)

    assert resumed.job_id == job.id
    with Session(engine) as session:
        row = PlanningJobRepository(session).get(job.id)
        assert row.status == "queued"
        assert row.progress_json == {"_resume_value": "single_city"}
        assert row.events[-1].event_type == "job_resumed"
