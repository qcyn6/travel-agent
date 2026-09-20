from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.destination import DestinationPlanResult
from backend.models.evaluation import EvaluationResult
from backend.models.degradation import DegradationNotice
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
    visit_duration_minutes: int = Field(default=90, ge=1, le=720)
    start_time: str | None = None
    end_time: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_duration_fields(cls, value):
        """Read old saved plans without bringing facts back into the model."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("visit_duration_minutes") and data.get("estimated_visit_minutes"):
            data["visit_duration_minutes"] = data["estimated_visit_minutes"]
        for legacy_key in (
            "estimated_visit_minutes",
            "duration_source",
            "opening_hours",
            "environment",
        ):
            data.pop(legacy_key, None)
        return data

    @property
    def estimated_visit_minutes(self) -> int:
        """Compatibility accessor for older internal callers and saved plans."""

        return self.visit_duration_minutes


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
    partial_knowledge_cities: list[str] = Field(default_factory=list)
    evaluation: EvaluationResult | None = None
    notices: list[DegradationNotice] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_replanning_fields(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for key in ("replanned_days", "replan_count", "replan_limit_reached"):
            data.pop(key, None)
        if isinstance(data.get("notices"), list):
            data["notices"] = [
                notice
                for notice in data["notices"]
                if not isinstance(notice, dict)
                or notice.get("code") != "replan_limit_reached"
            ]
        return data


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
