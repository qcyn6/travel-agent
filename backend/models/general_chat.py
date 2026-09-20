from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.route import RouteMode


GeneralIntent = Literal["knowledge", "route", "weather", "planning", "general"]
GeneralMissingField = Literal["origin", "destination", "city"]


class GeneralIntentDecision(BaseModel):
    """Transient routing result for one ordinary-chat message."""

    model_config = ConfigDict(extra="forbid")

    intent: GeneralIntent
    origin: str | None = None
    destination: str | None = None
    city: str | None = None
    target_date: date | None = None
    route_mode: RouteMode = "transit"
    missing_fields: list[GeneralMissingField] = Field(default_factory=list)


__all__ = ["GeneralIntent", "GeneralIntentDecision", "GeneralMissingField"]
