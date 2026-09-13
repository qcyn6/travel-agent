from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OpeningHoursStatus = Literal["known", "unknown"]
FactConfidence = Literal["high", "medium", "low", "unknown"]
DurationSource = Literal["web_extracted", "category_fallback"]
AttractionEnvironment = Literal["indoor", "outdoor", "mixed", "unknown"]


class OpeningPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekdays: list[int] = Field(default_factory=list)
    open_time: str
    close_time: str
    last_entry_time: str | None = None

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("weekday 必须在 1 到 7 之间")
        return sorted(set(value))

    @field_validator("open_time", "close_time", "last_entry_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("时间必须使用 HH:MM")
        hour, minute = map(int, parts)
        if hour > 23 or minute > 59:
            raise ValueError("时间必须使用 HH:MM")
        return f"{hour:02d}:{minute:02d}"


class OpeningHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OpeningHoursStatus = "unknown"
    periods: list[OpeningPeriod] = Field(default_factory=list)
    raw_text: str | None = None
    applicability_text: str | None = None
    confidence: FactConfidence = "unknown"
    source_url: str | None = None
    checked_at: datetime | None = None

    @model_validator(mode="after")
    def validate_known_periods(self):
        if self.status == "known" and not self.periods:
            raise ValueError("已知开放时间必须至少包含一个时段")
        return self


class AttractionFacts(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    poi_id: str
    environment: AttractionEnvironment = "unknown"
    estimated_visit_minutes: int = Field(ge=30, le=720)
    duration_source: DurationSource
    opening_hours: OpeningHours
    source_url: str | None = None
    confidence: FactConfidence = "unknown"
    checked_at: datetime
