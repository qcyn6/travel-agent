from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WeatherResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    date: Date | None = None
    condition: str | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=1)
    wind_level: str | None = None
    status: Literal["available", "skipped", "unavailable"]
    source: Literal["qweather", "none"]
