from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.requirement import TravelScope
from backend.models.travel_plan import TravelPlanResponse


class ExperimentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: Literal["single_day", "single_city_multi_day", "province_multi_city"]
    text: str
    travel_scope: TravelScope
    days: int = Field(ge=1)
    interests: list[str]


class BaselineAttraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    estimated_visit_minutes: int = Field(ge=30, le=720)


class BaselineDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    city: str
    attractions: list[BaselineAttraction] = Field(min_length=1)


class BaselinePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[BaselineDay] = Field(min_length=1)


class ExperimentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attraction_validity: float = Field(ge=0, le=1)
    interest_coverage: float = Field(ge=0, le=1)
    schedule_feasibility: float = Field(ge=0, le=1)
    route_reasonableness: float | None = Field(default=None, ge=0, le=1)
    external_constraint_satisfaction: float | None = Field(default=None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class ExperimentCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    case: ExperimentCase
    baseline_plan: BaselinePlan
    system_response: TravelPlanResponse
    baseline: ExperimentMetrics
    system: ExperimentMetrics
