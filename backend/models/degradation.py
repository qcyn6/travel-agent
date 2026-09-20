from typing import Literal

from pydantic import BaseModel, ConfigDict


DegradationCode = Literal[
    "route_estimated",
    "route_unavailable",
    "weather_skipped",
    "weather_unavailable",
    "knowledge_partial",
    "schedule_incomplete",
]


class DegradationNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: DegradationCode
    level: Literal["info", "warning"]
    day: int | None = None
    message: str
