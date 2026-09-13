from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.destination.planner import DestinationPlanResult
from backend.models.attraction_facts import OpeningHours
from backend.models.evaluation import EvaluationResult
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan
from backend.models.requirement import TravelRequirement, TravelScope
from backend.models.route import RouteSegment
from backend.models.schedule import DaySchedule
from backend.models.weather import WeatherResult


class AttractionStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    description: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    environment: Literal["indoor", "outdoor", "mixed", "unknown"] = "unknown"
    estimated_visit_minutes: int | None = Field(default=None, ge=1)
    duration_source: Literal["web_extracted", "category_fallback"] | None = None
    opening_hours: OpeningHours | None = None
    start_time: str | None = None
    end_time: str | None = None


class TravelDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    city: str
    city_adcode: str
    city_day: int = Field(ge=1)
    date: Date | None = None
    attractions: list[AttractionStop] = Field(min_length=1)
    routes: list[RouteSegment] = Field(default_factory=list)
    weather: WeatherResult | None = None
    schedule: DaySchedule | None = None
    description: str


class TravelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    city_stays: list[CityStay] = Field(min_length=1, max_length=3)
    city_plans: list[CityPlan] = Field(min_length=1, max_length=3)
    itinerary: list[TravelDay] = Field(min_length=1)
    evaluation: EvaluationResult | None = None


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
