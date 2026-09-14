from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.database.tables import ConversationRow, TravelPlanRow
from backend.models.evaluation import EvaluationResult
from backend.models.requirement import TravelRequirement
from backend.models.saved_plan import SavedTravelPlan
from backend.models.travel_plan import TravelPlan, TravelPlanResponse


class TravelPlanRepository:
    def __init__(self, session: Session):
        self.session = session

    def save(
        self,
        session_id: str,
        result: TravelPlanResponse,
        *,
        commit: bool = True,
    ) -> SavedTravelPlan:
        # Lock the owning conversation so version allocation is serialized even
        # when more than one API worker generates a plan for the same session.
        self.session.scalar(
            select(ConversationRow.id)
            .where(ConversationRow.id == session_id)
            .with_for_update()
        )
        latest = self.session.scalar(
            select(func.max(TravelPlanRow.version)).where(TravelPlanRow.session_id == session_id)
        )
        row = TravelPlanRow(
            session_id=session_id,
            version=int(latest or 0) + 1,
            requirement_json=result.requirement.model_dump(mode="json"),
            plan_json=result.plan.model_dump(mode="json"),
            evaluation_json=(
                result.plan.evaluation.model_dump(mode="json")
                if result.plan.evaluation is not None
                else None
            ),
        )
        try:
            self.session.add(row)
            if commit:
                self.session.commit()
            else:
                self.session.flush()
            self.session.refresh(row)
        except Exception:
            self.session.rollback()
            raise
        return _to_model(row)

    def list_for_session(self, session_id: str) -> list[SavedTravelPlan]:
        rows = self.session.scalars(
            select(TravelPlanRow)
            .where(TravelPlanRow.session_id == session_id)
            .order_by(TravelPlanRow.version.desc())
        )
        return [_to_model(row) for row in rows]


def _to_model(row: TravelPlanRow) -> SavedTravelPlan:
    return SavedTravelPlan(
        id=row.id,
        session_id=row.session_id,
        version=row.version,
        requirement=TravelRequirement.model_validate(row.requirement_json),
        plan=TravelPlan.model_validate(row.plan_json),
        evaluation=(
            EvaluationResult.model_validate(row.evaluation_json)
            if row.evaluation_json is not None
            else None
        ),
        created_at=row.created_at,
    )


def get_travel_plan_repository(session: Session = Depends(get_db)) -> TravelPlanRepository:
    return TravelPlanRepository(session)
