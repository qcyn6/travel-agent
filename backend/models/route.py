from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteMode = Literal["walking", "transit", "driving"]
# `amap` is retained for historical plan snapshots. New plans use the MCP
# result when available and a haversine-based estimate as a bounded fallback.
RouteSource = Literal["amap_mcp", "amap", "haversine", "unavailable"]


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
    origin_city: str | None = None
    destination_city: str | None = None
    distance_m: int | None = Field(default=None, ge=0)
    duration_min: int | None = Field(default=None, ge=0)
    mode: RouteMode
    source: RouteSource
    estimated: bool = False
    # Real Amap route geometry when the provider response contains step
    # polylines. Historical and estimated routes may leave it empty.
    polyline: list[tuple[float, float]] = Field(default_factory=list)
