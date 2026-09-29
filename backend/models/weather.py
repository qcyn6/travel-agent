from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DailyWeatherAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    advice: str = Field(min_length=1, max_length=240)


class WeatherResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    date: Date | None = None
    condition: str | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=1)
    wind_level: str | None = None
    travel_advice: str | None = Field(default=None, max_length=240)
    travel_advice_source: Literal["ai", "rule_based"] | None = None
    status: Literal["available", "skipped", "unavailable"]
    source: Literal["amap_mcp", "qweather", "none"]
