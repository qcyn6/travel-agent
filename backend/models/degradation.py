from typing import Literal

from pydantic import BaseModel, ConfigDict


DegradationCode = Literal[
    "route_estimated",
    "route_unavailable",
    "weather_skipped",
    "weather_unavailable",
    "opening_hours_unknown",
    "schedule_incomplete",
    "replan_limit_reached",
]


class DegradationNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: DegradationCode
    level: Literal["info", "warning"]
    day: int | None = None
    message: str
