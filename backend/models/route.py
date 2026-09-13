from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteMode = Literal["walking", "transit", "driving"]
RouteSource = Literal["amap", "haversine", "unavailable"]


class RouteSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_poi_id: str
    origin_name: str
    destination_poi_id: str
    destination_name: str
    origin_longitude: float | None = None
    origin_latitude: float | None = None
    destination_longitude: float | None = None
    destination_latitude: float | None = None
    distance_m: int | None = Field(default=None, ge=0)
    duration_min: int | None = Field(default=None, ge=0)
    mode: RouteMode
    source: RouteSource
    estimated: bool = False

