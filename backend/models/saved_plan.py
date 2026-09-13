from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.models.evaluation import EvaluationResult
from backend.models.requirement import TravelRequirement
from backend.models.travel_plan import TravelPlan


class SavedTravelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    session_id: str
    version: int
    requirement: TravelRequirement
    plan: TravelPlan
    evaluation: EvaluationResult | None = None
    created_at: datetime
