from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.database.tables import PlanningEventRow, PlanningJobRow
from backend.models.conversation import RequirementDraft


ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})


class PlanningJobRepository:
    """Persistence boundary for planning jobs and replayable progress events."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, job_id: str, *, for_update: bool = False) -> PlanningJobRow | None:
        query = select(PlanningJobRow).where(PlanningJobRow.id == job_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def latest_for_conversation(
        self,
        conversation_id: str,
        *,
        for_update: bool = False,
    ) -> PlanningJobRow | None:
        query = (
            select(PlanningJobRow)
            .where(PlanningJobRow.conversation_id == conversation_id)
            .order_by(PlanningJobRow.created_at.desc(), PlanningJobRow.id.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def active_for_conversation(
        self,
        conversation_id: str,
        *,
        for_update: bool = False,
    ) -> PlanningJobRow | None:
        query = (
            select(PlanningJobRow)
            .where(
                PlanningJobRow.conversation_id == conversation_id,
                PlanningJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(PlanningJobRow.created_at.desc(), PlanningJobRow.id.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def create(self, conversation_id: str, requirement: RequirementDraft) -> PlanningJobRow:
        row = PlanningJobRow(
            id=str(uuid4()),
            conversation_id=conversation_id,
            status="queued",
            requirement_json=requirement.model_dump(mode="json"),
            progress_json={},
            retry_count=0,
        )
        self.session.add(row)
        return row

    def list_queued(self, *, limit: int = 100) -> list[PlanningJobRow]:
        return list(
            self.session.scalars(
                select(PlanningJobRow)
                .where(PlanningJobRow.status == "queued")
                .order_by(PlanningJobRow.created_at, PlanningJobRow.id)
                .limit(max(1, limit))
            )
        )

    def list_recoverable(self, *, limit: int = 100, stale_after_seconds: float = 90) -> list[PlanningJobRow]:
        cutoff = datetime.now() - timedelta(seconds=max(1, stale_after_seconds))
        return list(self.session.scalars(
            select(PlanningJobRow).where(or_(
                PlanningJobRow.status == "queued",
                and_(PlanningJobRow.status == "running",
                     func.coalesce(PlanningJobRow.heartbeat_at, PlanningJobRow.started_at,
                                   PlanningJobRow.created_at) <= cutoff),
            )).order_by(PlanningJobRow.created_at, PlanningJobRow.id).limit(max(1, limit))
        ))

    def set_requirement(self, row: PlanningJobRow, requirement: RequirementDraft) -> None:
        row.requirement_json = requirement.model_dump(mode="json")

    @staticmethod
    def is_stale(
        row: PlanningJobRow,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        if row.status != "running":
            return False
        heartbeat = row.heartbeat_at or row.started_at or row.created_at
        if heartbeat is None:
            return True
        current_time = now or datetime.now()
        return current_time - heartbeat >= timedelta(seconds=max(0.1, stale_after_seconds))

    def claim(
        self,
        row: PlanningJobRow,
        *,
        worker_id: str,
        stale_after_seconds: float,
    ) -> bool:
        if row.status in TERMINAL_JOB_STATUSES:
            return False
        if row.status == "running" and not self.is_stale(
            row,
            stale_after_seconds=stale_after_seconds,
        ):
            return False
        self.mark_running(row, worker_id=worker_id)
        return True

    @staticmethod
    def owns(row: PlanningJobRow, worker_id: str) -> bool:
        return row.worker_id == worker_id

    def mark_running(self, row: PlanningJobRow, *, worker_id: str | None = None) -> None:
        now = datetime.now()
        row.status = "running"
        row.started_at = row.started_at or now
        row.heartbeat_at = now
        row.finished_at = None
        row.error = None
        row.retry_count = (row.retry_count or 0) + 1
        row.worker_id = worker_id

    def record_progress(
        self,
        row: PlanningJobRow,
        *,
        step: str,
        label: str,
        status: str,
        worker_id: str | None = None,
    ) -> PlanningEventRow | None:
        if worker_id is not None and not self.owns(row, worker_id):
            return None
        row.current_step = step
        row.progress_json = {"step": step, "label": label, "status": status}
        row.heartbeat_at = datetime.now()
        return self.append_event(
            row,
            "planning_progress",
            {"step": step, "label": label, "status": status},
        )

    def touch_heartbeat(self, row: PlanningJobRow, *, worker_id: str) -> bool:
        if row.status not in ACTIVE_JOB_STATUSES or not self.owns(row, worker_id):
            return False
        row.heartbeat_at = datetime.now()
        return True

    def mark_completed(self, row: PlanningJobRow, *, worker_id: str | None = None) -> bool:
        if worker_id is not None and not self.owns(row, worker_id):
            return False
        now = datetime.now()
        row.status = "completed"
        row.current_step = "complete"
        row.heartbeat_at = now
        row.finished_at = now
        row.error = None
        row.worker_id = None
        return True

    def mark_failed(
        self,
        row: PlanningJobRow,
        message: str,
        *,
        worker_id: str | None = None,
    ) -> bool:
        if worker_id is not None and not self.owns(row, worker_id):
            return False
        now = datetime.now()
        row.status = "failed"
        row.heartbeat_at = now
        row.finished_at = now
        row.error = message[:2000]
        row.worker_id = None
        return True

    def append_event(
        self,
        row: PlanningJobRow,
        event_type: str,
        payload: dict,
    ) -> PlanningEventRow:
        # Progress events are written from short-lived sessions while the
        # workflow session may stay open for minutes. Lock the job row and use
        # a locking read for the latest sequence so MySQL's repeatable-read
        # snapshot cannot allocate an old sequence number at completion.
        self.session.flush()
        self.session.execute(
            select(PlanningJobRow.id)
            .where(PlanningJobRow.id == row.id)
            .with_for_update()
        )
        next_sequence = self.session.scalar(
            select(PlanningEventRow.sequence_number)
            .where(PlanningEventRow.job_id == row.id)
            .order_by(PlanningEventRow.sequence_number.desc())
            .limit(1)
            .with_for_update()
        )
        event = PlanningEventRow(
            job_id=row.id,
            sequence_number=int(next_sequence or 0) + 1,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        return event

    def list_events(self, job_id: str, *, after: int = 0) -> list[PlanningEventRow]:
        return list(
            self.session.scalars(
                select(PlanningEventRow)
                .where(
                    PlanningEventRow.job_id == job_id,
                    PlanningEventRow.sequence_number > after,
                )
                .order_by(PlanningEventRow.sequence_number)
            )
        )
