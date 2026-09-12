from pydantic import BaseModel, ConfigDict, Field

from backend.destination.planner import DestinationPlanResult
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan
from backend.models.requirement import TravelRequirement, TravelScope


class TravelDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    city: str
    city_day: int = Field(ge=1)
    attractions: list[str] = Field(min_length=1)
    description: str


class TravelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    city_stays: list[CityStay] = Field(min_length=1, max_length=3)
    city_plans: list[CityPlan] = Field(min_length=1, max_length=3)
    itinerary: list[TravelDay] = Field(min_length=1)


class TravelPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    travel_scope: TravelScope | None = None


class TravelPlanResponse(BaseModel):
    requirement: TravelRequirement
    plan: TravelPlan


class DestinationChoiceResponse(BaseModel):
    requirement: TravelRequirement
    destination_options: DestinationPlanResult
