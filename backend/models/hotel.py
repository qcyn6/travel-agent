from typing import Literal

from pydantic import BaseModel, ConfigDict


HotelSource = Literal["amap", "amap_mcp", "unavailable"]


class HotelStay(BaseModel):
    """One overnight hotel selected for a travel day."""

    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    address: str | None = None
    rating: float | None = None
    longitude: float | None = None
    latitude: float | None = None
    image_url: str | None = None
    source: HotelSource = "amap"
