from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from backend.models.evaluation import EvaluationResult
from backend.models.degradation import DegradationNotice
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan
from backend.models.hotel import HotelStay
from backend.models.requirement import TravelRequirement, TravelScope
from backend.models.route import RouteSegment
from backend.models.schedule import DaySchedule
from backend.models.weather import WeatherResult
from backend.models.retrieval import HybridSearchResult


class AttractionStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    description: str | None = None
    # Display-only photo resolved after the final itinerary is selected. It is
    # never written back to the attractions table.
    image_url: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    visit_duration_minutes: int = Field(default=90, ge=1, le=720)
    opening_hours_weekly: str | None = None
    opening_hours_today: str | None = None
    opening_hours_status: Literal["available", "unavailable", "not_checked"] = "not_checked"
    opening_hours_applied: bool = False
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
    # The previous night's hotel starts this day's route and its travel time
    # is included in this day's window. The current night's hotel is an
    # outbound overnight leg and remains outside today's window.
    start_hotel: HotelStay | None = None
    start_hotel_route: RouteSegment | None = None
    hotel: HotelStay | None = None
    hotel_route: RouteSegment | None = None
    weather: WeatherResult | None = None
    schedule: DaySchedule | None = None
    description: str


class TravelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    city_stays: list[CityStay] = Field(min_length=1, max_length=3)
    city_plans: list[CityPlan] = Field(min_length=1, max_length=3)
    itinerary: list[TravelDay] = Field(min_length=1)
    # Keep the city-level retrieval pool with the saved plan so the user can
    # manually swap a stop after generation without running retrieval again.
    replacement_candidates_by_city: dict[str, list[HybridSearchResult]] = Field(
        default_factory=dict
    )
    manual_adjustment_warnings: dict[int, list[str]] = Field(default_factory=dict)
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


class AttractionReplacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    current_poi_id: str = Field(min_length=1)
    replacement_poi_id: str = Field(min_length=1)


class AttractionReplacementOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: HybridSearchResult
    distance_m: float | None = Field(default=None, ge=0)
    distance_score: float = Field(default=0, ge=0)
    priority_score: float = Field(default=0, ge=0)


class AttractionReplacementPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: TravelPlanResponse
    warnings: list[str] = Field(default_factory=list)
