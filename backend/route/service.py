import math

from backend.models.requirement import TransportMode
from backend.models.route import RouteMode, RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.route.amap import RouteAPIError, RouteConfigurationError


_AVERAGE_SPEED_KMH: dict[RouteMode, float] = {
    "walking": 4.5,
    "transit": 20.0,
    "driving": 30.0,
}


class RouteEnrichmentService:
    def __init__(self, client):
        self.client = client

    async def enrich_day(self, day: TravelDay, *, requested_mode: TransportMode) -> TravelDay:
        routes: list[RouteSegment] = []
        for origin, destination in zip(day.attractions, day.attractions[1:]):
            mode = _resolve_mode(origin, destination, requested_mode)
            if not _has_coordinates(origin, destination):
                routes.append(_unavailable_segment(origin, destination, mode))
                continue
            try:
                route = await self.client.get_route(
                    origin,
                    destination,
                    mode=mode,
                    city_adcode=day.city_adcode,
                )
            except (RouteAPIError, RouteConfigurationError):
                route = _fallback_segment(origin, destination, mode)
            routes.append(route)
        return day.model_copy(update={"routes": routes})


def haversine_distance_m(
    origin_longitude: float,
    origin_latitude: float,
    destination_longitude: float,
    destination_latitude: float,
) -> int:
    radius_m = 6_371_000
    latitude_1 = math.radians(origin_latitude)
    latitude_2 = math.radians(destination_latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(destination_longitude - origin_longitude)
    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(delta_longitude / 2) ** 2
    )
    return round(radius_m * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)))


def _resolve_mode(
    origin: AttractionStop,
    destination: AttractionStop,
    requested_mode: TransportMode,
) -> RouteMode:
    if requested_mode != "auto":
        return requested_mode
    if not _has_coordinates(origin, destination):
        return "transit"
    distance = haversine_distance_m(
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    )
    return "walking" if distance <= 1_500 else "transit"


def _has_coordinates(origin: AttractionStop, destination: AttractionStop) -> bool:
    return None not in (
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    )


def _fallback_segment(
    origin: AttractionStop,
    destination: AttractionStop,
    mode: RouteMode,
) -> RouteSegment:
    straight_distance = haversine_distance_m(
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    )
    distance_factor = 1.1 if mode == "walking" else 1.25
    estimated_distance = round(straight_distance * distance_factor)
    speed_m_per_minute = _AVERAGE_SPEED_KMH[mode] * 1000 / 60
    return _segment(
        origin,
        destination,
        mode,
        source="haversine",
        distance_m=estimated_distance,
        duration_min=max(1, math.ceil(estimated_distance / speed_m_per_minute)),
        estimated=True,
    )


def _unavailable_segment(
    origin: AttractionStop,
    destination: AttractionStop,
    mode: RouteMode,
) -> RouteSegment:
    return _segment(origin, destination, mode, source="unavailable")


def _segment(
    origin: AttractionStop,
    destination: AttractionStop,
    mode: RouteMode,
    *,
    source: str,
    distance_m: int | None = None,
    duration_min: int | None = None,
    estimated: bool = False,
) -> RouteSegment:
    return RouteSegment(
        origin_poi_id=origin.poi_id,
        origin_name=origin.name,
        destination_poi_id=destination.poi_id,
        destination_name=destination.name,
        origin_longitude=origin.longitude,
        origin_latitude=origin.latitude,
        destination_longitude=destination.longitude,
        destination_latitude=destination.latitude,
        distance_m=distance_m,
        duration_min=duration_min,
        mode=mode,
        source=source,
        estimated=estimated,
    )
