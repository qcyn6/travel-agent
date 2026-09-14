from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.tables import PlanningEventRow, PlanningJobRow
from backend.models.conversation import RequirementDraft


ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled", "waiting_for_choice"})


class PlanningJobRepository:
    """Persistence boundary for planning jobs and replayable progress events."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, job_id: str, *, for_update: bool = False) -> PlanningJobRow | None:
        query = select(PlanningJobRow).where(PlanningJobRow.id == job_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def latest_for_conversation(self, conversation_id: str) -> PlanningJobRow | None:
        return self.session.scalar(
            select(PlanningJobRow)
            .where(PlanningJobRow.conversation_id == conversation_id)
            .order_by(PlanningJobRow.created_at.desc(), PlanningJobRow.id.desc())
            .limit(1)
        )

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

    def set_requirement(self, row: PlanningJobRow, requirement: RequirementDraft) -> None:
        row.requirement_json = requirement.model_dump(mode="json")

    def mark_running(self, row: PlanningJobRow) -> None:
        now = datetime.now()
        row.status = "running"
        row.started_at = row.started_at or now
        row.heartbeat_at = now
        row.finished_at = None
        row.error = None
        row.retry_count = (row.retry_count or 0) + 1

    def record_progress(
        self,
        row: PlanningJobRow,
        *,
        step: str,
        label: str,
        status: str,
    ) -> PlanningEventRow:
        row.current_step = step
        row.progress_json = {"step": step, "label": label, "status": status}
        row.heartbeat_at = datetime.now()
        return self.append_event(
            row,
            "planning_progress",
            {"step": step, "label": label, "status": status},
        )

    def mark_completed(self, row: PlanningJobRow) -> None:
        now = datetime.now()
        row.status = "completed"
        row.current_step = "complete"
        row.heartbeat_at = now
        row.finished_at = now
        row.error = None

    def mark_waiting_for_choice(self, row: PlanningJobRow) -> None:
        now = datetime.now()
        row.status = "waiting_for_choice"
        row.heartbeat_at = now
        row.finished_at = now
        row.error = None

    def mark_failed(self, row: PlanningJobRow, message: str) -> None:
        now = datetime.now()
        row.status = "failed"
        row.heartbeat_at = now
        row.finished_at = now
        row.error = message[:2000]

    def append_event(
        self,
        row: PlanningJobRow,
        event_type: str,
        payload: dict,
    ) -> PlanningEventRow:
        next_sequence = self.session.scalar(
            select(func.coalesce(func.max(PlanningEventRow.sequence_number), 0) + 1).where(
                PlanningEventRow.job_id == row.id
            )
        )
        event = PlanningEventRow(
            job_id=row.id,
            sequence_number=int(next_sequence or 1),
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
