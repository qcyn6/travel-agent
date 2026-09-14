from contextlib import contextmanager

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


def test_prepare_conversation_reuses_the_active_job(monkeypatch):
    import backend.application.planning as planning_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        conversation = ConversationRepository(session).create()
        requirement = RequirementDraft(province="浙江省", days=1, interests=["自然"])
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
