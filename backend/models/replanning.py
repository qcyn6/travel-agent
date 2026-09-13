from pydantic import BaseModel, ConfigDict, Field

from backend.models.travel_plan import TravelPlan


class DayReplanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    attraction_poi_ids: list[str] = Field(min_length=1, max_length=6)
    description: str = Field(min_length=10)


class ReplanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: TravelPlan
    changed_days: list[int]
    used_fallback: bool = False
