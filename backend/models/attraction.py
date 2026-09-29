from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


KnowledgeStatus = Literal["pending", "processing", "staged", "ready", "failed"]
OpeningHoursStatus = Literal["available", "unavailable", "not_checked"]


class AttractionPOI(BaseModel):
    """Attraction candidate returned by the local catalog or an external POI provider."""

    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    # Amap does not provide this value. It is carried only when the candidate
    # came from the local catalog, whose attraction row owns the value.
    visit_duration_minutes: int | None = Field(default=None, ge=1, le=720)
    # These fields are kept for external Amap response compatibility. Address
    # and rating are intentionally not part of the local planning schema.
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    rating: float | None = None
    opening_hours_weekly: str | None = None
    opening_hours_today: str | None = None
    opening_hours_status: OpeningHoursStatus = "not_checked"


class AttractionKnowledgeState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    poi_id: str
    name: str
    knowledge_status: KnowledgeStatus
    knowledge_error: str | None = None
