from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from backend.time_utils import normalize_clock_time


TravelScope = Literal["single_city", "multi_city", "auto"]
TransportMode = Literal["auto", "walking", "transit", "driving"]

# Keep the product boundary in one place. The planner is an itinerary engine
# for a short, reviewable trip rather than an open-ended long-stay planner.
MIN_TRAVEL_DAYS = 1
MAX_TRAVEL_DAYS = 7
MAX_SINGLE_CITY_DAYS = 3
# A multi-city plan is intentionally a different product mode from a short
# single-city stay. Keep this boundary next to the other product limits so the
# parser, conversation flow, destination planner and API do not drift apart.
MIN_MULTI_CITY_TRIP_DAYS = MAX_SINGLE_CITY_DAYS + 1
MIN_MULTI_CITY_DAYS = 1
MAX_TRIP_CITIES = 3


def validate_travel_days(value: object, *, allow_legacy_days: bool = False) -> object:
    """Validate a day count before Pydantic emits a low-level error message."""

    if value is None or isinstance(value, bool):
        return value
    numeric_value: int | None = None
    if isinstance(value, int):
        numeric_value = value
    elif isinstance(value, str) and value.strip().isdigit():
        numeric_value = int(value.strip())
    elif isinstance(value, float) and value.is_integer():
        numeric_value = int(value)
    if (
        numeric_value is not None
        and allow_legacy_days
        and numeric_value > MAX_TRAVEL_DAYS
    ):
        return value
    if numeric_value is not None and not MIN_TRAVEL_DAYS <= numeric_value <= MAX_TRAVEL_DAYS:
        raise ValueError(
            f"旅行天数必须在{MIN_TRAVEL_DAYS}～{MAX_TRAVEL_DAYS}天之间"
        )
    return value


def validate_start_date(
    value: object,
    *,
    reference_date: date | None = None,
    allow_past_dates: bool = False,
) -> object:
    """Reject a newly submitted departure date that is already in the past.

    This helper intentionally does not replace Pydantic's date parsing. Invalid
    date strings are returned unchanged so Pydantic can produce its normal
    validation error; the helper only owns the product boundary.
    """

    if value is None or allow_past_dates:
        return value
    candidate = value if isinstance(value, date) else None
    if candidate is None and isinstance(value, str):
        try:
            candidate = date.fromisoformat(value.strip())
        except ValueError:
            return value
    if candidate is not None and candidate <= (reference_date or date.today()):
        raise ValueError("出发日期必须从明天开始")
    return value


def scope_day_boundary_error(
    days: int | None,
    travel_scope: TravelScope,
    *,
    city_count: int = 0,
) -> str | None:
    """Return the shared travel-scope/day validation message, if invalid.

    ``auto`` without a resolved city list is deliberately left permissive
    because it represents an unfinished conversation. Callers that already
    resolved one or more cities pass ``city_count`` so the same strict product
    boundary applies before execution.
    """

    if days is None:
        return None
    effective_scope = travel_scope
    if travel_scope == "auto" and city_count == 1:
        effective_scope = "single_city"
    elif travel_scope == "auto" and city_count >= 2:
        effective_scope = "multi_city"
    if effective_scope == "single_city" and days > MAX_SINGLE_CITY_DAYS:
        return (
            f"单城市规划目前只支持{MIN_TRAVEL_DAYS}～{MAX_SINGLE_CITY_DAYS}天；"
            f"{MIN_MULTI_CITY_TRIP_DAYS}～{MAX_TRAVEL_DAYS}天请改为同省多城市"
        )
    if effective_scope == "multi_city" and days < MIN_MULTI_CITY_TRIP_DAYS:
        return (
            f"{MIN_TRAVEL_DAYS}～{MAX_SINGLE_CITY_DAYS}天只支持单城市规划；"
            f"同省多城市至少需要{MIN_MULTI_CITY_TRIP_DAYS}天，"
            f"请把天数增加到{MIN_MULTI_CITY_TRIP_DAYS}～{MAX_TRAVEL_DAYS}天"
        )
    if city_count >= 2 and days < city_count:
        return "多城市规划至少需要为每个城市安排1天：2个城市至少2天，3个城市至少3天"
    return None


def validate_scope_day_boundary(
    days: int | None,
    travel_scope: TravelScope,
    *,
    city_count: int = 0,
) -> None:
    """Raise ``ValueError`` when a settled scope violates the product rule."""

    message = scope_day_boundary_error(days, travel_scope, city_count=city_count)
    if message:
        raise ValueError(message)


class TravelRequirement(BaseModel):
    """The smallest stable requirement contract used by the MVP workflow."""

    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    cities: list[str] = Field(default_factory=list)
    # A destination can be settled either by naming a province or by explicitly
    # asking the planner to recommend one from the user's interests/preferences.
    province_preference_confirmed: bool = False
    # A province-only plan must explicitly settle whether the user names the
    # cities or wants the planner to recommend them.
    city_preference_confirmed: bool = False
    days: int | None = Field(
        default=None,
        ge=MIN_TRAVEL_DAYS,
        description=f"单次规划支持 {MIN_TRAVEL_DAYS}～{MAX_TRAVEL_DAYS} 天",
        json_schema_extra={"maximum": MAX_TRAVEL_DAYS},
    )
    interests: list[str] = Field(default_factory=list)
    preference_text: str | None = None
    travel_scope: TravelScope = "auto"
    start_date: date | None = None
    daily_start_time: str | None = None
    daily_end_time: str | None = None
    transport_mode: TransportMode = "auto"

    @field_validator("days", mode="before")
    @classmethod
    def validate_day_range(cls, value: object, info: ValidationInfo) -> object:
        context = info.context or {}
        return validate_travel_days(
            value,
            allow_legacy_days=bool(context.get("allow_legacy_days")),
        )

    @field_validator("province", "preference_text", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("cities", "interests")
    @classmethod
    def deduplicate_text_items(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    @field_validator("daily_start_time", "daily_end_time", mode="before")
    @classmethod
    def normalize_daily_times(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name or "每日时间"
        label = "每日开始时间" if field_name == "daily_start_time" else "每日结束时间"
        return normalize_clock_time(value, label=label)

    @model_validator(mode="after")
    def validate_scope_day_boundary(self):
        if self.daily_start_time and self.daily_end_time and self.daily_start_time >= self.daily_end_time:
            raise ValueError("每日结束时间必须晚于开始时间，且不支持跨日安排")
        if self.days is None:
            return self
        # A single named city with ``auto`` is already an implicit single-city
        # choice. Keep that historical convenience while applying the shared
        # boundary to explicit ``single_city``/``multi_city`` selections.
        settled_scope = self.travel_scope
        if settled_scope == "auto" and len(self.cities) == 1:
            settled_scope = "single_city"
        # Keep the resolver's more specific “one city vs multi-city” error for
        # direct callers that construct this legacy-conflicting combination.
        # Parsed user text is still rejected earlier by RequirementParser, and
        # the resolver/planner enforce the same boundary before execution.
        if settled_scope == "multi_city" and len(self.cities) == 1:
            settled_scope = "auto"
        validate_scope_day_boundary(
            self.days,
            settled_scope,
            city_count=len(self.cities) if settled_scope != "auto" else 0,
        )
        return self

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date_boundary(cls, value: object, info: ValidationInfo) -> object:
        context = info.context or {}
        return validate_start_date(
            value,
            reference_date=context.get("reference_date"),
            allow_past_dates=bool(context.get("allow_past_dates")),
        )
