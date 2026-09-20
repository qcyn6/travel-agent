from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.city_stay import CityStay
from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult


class DayPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    attractions: list[str] = Field(min_length=1)
    description: str = Field(min_length=10)


class CityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    days: list[DayPlan] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_day_numbers(self):
        if [day.day for day in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError("行程天数必须从 Day 1 连续编号")
        return self


class CityPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: TravelRequirement
    city_stay: CityStay
    top_k: int = Field(default=20, ge=1, le=20)


class CityPlanningResult(BaseModel):
    city_stay: CityStay
    attractions: list[HybridSearchResult]
    city_plan: CityPlan
