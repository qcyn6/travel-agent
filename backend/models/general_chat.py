from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.route import RouteMode


GeneralIntent = Literal[
    "knowledge",
    "route",
    "weather",
    "nearby",
    "planning",
    "general",
    "out_of_scope",
]
GeneralMissingField = Literal[
    "origin",
    "destination",
    "city",
    "nearby_center",
    "nearby_category",
]


class GeneralIntentDecision(BaseModel):
    """Transient routing result for one ordinary-chat message."""

    model_config = ConfigDict(extra="forbid")

    intent: GeneralIntent
    # ``intent`` remains the primary intent for backwards compatibility.  A
    # message may also carry secondary intents, for example a knowledge
    # question that asks for tomorrow's weather in the same sentence.
    intents: list[GeneralIntent] = Field(default_factory=list)
    need_retrieval: bool = False
    origin: str | None = None
    destination: str | None = None
    city: str | None = None
    nearby_center: str | None = None
    nearby_category: str | None = None
    target_date: date | None = None
    forecast_dates: list[date] = Field(default_factory=list)
    route_mode: RouteMode = "transit"
    missing_fields: list[GeneralMissingField] = Field(default_factory=list)

    def includes(self, intent: GeneralIntent) -> bool:
        """Return whether this decision contains ``intent``."""

        return self.intent == intent or intent in self.intents


__all__ = ["GeneralIntent", "GeneralIntentDecision", "GeneralMissingField"]
