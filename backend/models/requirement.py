from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TravelScope = Literal["single_city", "multi_city", "auto"]
TransportMode = Literal["auto", "walking", "transit", "driving"]


class TravelRequirement(BaseModel):
    """The smallest stable requirement contract used by the MVP workflow."""

    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    cities: list[str] = Field(default_factory=list)
    days: int | None = Field(default=None, ge=1)
    interests: list[str] = Field(default_factory=list)
    preference_text: str | None = None
    travel_scope: TravelScope = "auto"
    start_date: date | None = None
    transport_mode: TransportMode = "auto"

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
