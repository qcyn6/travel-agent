import asyncio
import logging
import math

from backend.models.requirement import TransportMode
from backend.models.route import RouteMode, RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.route.amap_mcp import RouteAPIError, RouteConfigurationError

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000
_ROAD_DETOUR_FACTOR = 1.35
_ESTIMATE_SPEED_KMH = {
    "walking": 4.5,
    "transit": 22.0,
    "driving": 30.0,
}
_ESTIMATE_TRANSFER_MINUTES = {
    "walking": 0,
    "transit": 10,
    "driving": 5,
}


class RouteEnrichmentService:
    def __init__(self, client: object | None, *, max_concurrency: int = 3):
        self.client = client
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def enrich_day(self, day: TravelDay, *, requested_mode: TransportMode) -> TravelDay:
        routes = await asyncio.gather(
            *(
                self.resolve_segment(
                    origin,
                    destination,
                    requested_mode=requested_mode,
                    city_adcode=day.city_adcode,
                )
                for origin, destination in zip(day.attractions, day.attractions[1:])
            )
        )
        return day.model_copy(update={"routes": routes})

    async def resolve_segment(
        self,
        origin: AttractionStop,
        destination: AttractionStop,
        *,
        requested_mode: TransportMode,
        city_adcode: str,
    ) -> RouteSegment:
        """Resolve one adjacent-attraction route with the normal fallbacks.

        The public single-segment boundary lets the planner validate a
        candidate before selecting it.  ``enrich_day`` intentionally reuses
        the same path so route semantics stay identical in both workflows.
        """

        return await self._enrich_segment(
            origin,
            destination,
            requested_mode=requested_mode,
            city_adcode=city_adcode,
        )

    async def _enrich_segment(
        self,
        origin: AttractionStop,
        destination: AttractionStop,
        *,
        requested_mode: TransportMode,
        city_adcode: str,
    ) -> RouteSegment:
        # Keep the distance decision and the route request for one segment in
        # the same bounded slot. Different segments remain independent.
        async with self._semaphore:
            if not _has_coordinates(origin, destination):
                return _unavailable_segment(
                    origin,
                    destination,
                    _display_mode(requested_mode),
                )
            if self.client is None:
                return _estimated_segment(
                    origin,
                    destination,
                    _fallback_mode(requested_mode, origin, destination),
                )

            mode = await _resolve_mode_with_client(
                origin,
                destination,
                requested_mode,
                self.client,
            )
            if mode is None:
                mode = _fallback_mode(requested_mode, origin, destination)
                logger.warning(
                    "高德 MCP 距离不可用，使用本地距离估算交通方式：%s -> %s (%s)",
                    origin.poi_id,
                    destination.poi_id,
                    mode,
                )
            try:
                return await self.client.get_route(
                    origin,
                    destination,
                    mode=mode,
                    city_adcode=city_adcode,
                )
            except (RouteAPIError, RouteConfigurationError) as exc:
                logger.warning(
                    "高德 MCP 路线不可用，使用距离估算：%s -> %s (%s)",
                    origin.poi_id,
                    destination.poi_id,
                    type(exc).__name__,
                )
                return _estimated_segment(origin, destination, mode)
            except Exception as exc:
                logger.warning(
                    "高德 MCP 路线出现未预期错误，使用距离估算：%s -> %s (%s)",
                    origin.poi_id,
                    destination.poi_id,
                    type(exc).__name__,
                )
                return _estimated_segment(origin, destination, mode)


async def _resolve_mode_with_client(
    origin: AttractionStop,
    destination: AttractionStop,
    requested_mode: TransportMode,
    client,
) -> RouteMode | None:
    """Resolve ``auto`` only through the Amap MCP distance operation."""

    if requested_mode != "auto":
        return requested_mode
    if not _has_coordinates(origin, destination) or client is None:
        return None
    get_distance = getattr(client, "get_distance", None)
    if get_distance is None:
        logger.warning(
            "高德 MCP 客户端不支持距离操作，使用本地距离估算交通方式：%s -> %s",
            origin.poi_id,
            destination.poi_id,
        )
        return None
    try:
        distance = float(await get_distance(origin, destination))
    except (RouteAPIError, RouteConfigurationError) as exc:
        logger.warning(
            "高德 MCP 距离不可用，使用本地距离估算交通方式：%s -> %s (%s)",
            origin.poi_id,
            destination.poi_id,
            type(exc).__name__,
        )
        return None
    except Exception as exc:
        logger.warning(
            "高德 MCP 距离出现未预期错误，使用本地距离估算交通方式：%s -> %s (%s)",
            origin.poi_id,
            destination.poi_id,
            type(exc).__name__,
        )
        return None
    return "walking" if distance <= 1_500 else "transit"


def _has_coordinates(origin: AttractionStop, destination: AttractionStop) -> bool:
    return None not in (
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    )


def _display_mode(requested_mode: TransportMode) -> RouteMode:
    """Map ``auto`` to a valid display mode when no route was obtained."""

    return "transit" if requested_mode == "auto" else requested_mode


def _fallback_mode(
    requested_mode: TransportMode,
    origin: AttractionStop,
    destination: AttractionStop,
) -> RouteMode:
    if requested_mode != "auto":
        return requested_mode
    return "walking" if _straight_line_distance_m(origin, destination) <= 1_500 else "transit"


def _unavailable_segment(
    origin: AttractionStop,
    destination: AttractionStop,
    mode: RouteMode,
) -> RouteSegment:
    return _segment(origin, destination, mode, source="unavailable")


def _estimated_segment(
    origin: AttractionStop,
    destination: AttractionStop,
    mode: RouteMode,
) -> RouteSegment:
    straight_line_distance_m = _straight_line_distance_m(origin, destination)
    estimated_distance_m = max(1, round(straight_line_distance_m * _ROAD_DETOUR_FACTOR))
    speed_kmh = _ESTIMATE_SPEED_KMH[mode]
    transfer_minutes = _ESTIMATE_TRANSFER_MINUTES[mode]
    estimated_duration_min = max(
        1,
        math.ceil(estimated_distance_m / 1000 / speed_kmh * 60 + transfer_minutes),
    )
    return _segment(
        origin,
        destination,
        mode,
        source="haversine",
        distance_m=estimated_distance_m,
        duration_min=estimated_duration_min,
        estimated=True,
    )


def _straight_line_distance_m(origin: AttractionStop, destination: AttractionStop) -> float:
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    origin_latitude = math.radians(origin.latitude)
    destination_latitude = math.radians(destination.latitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude)
        * math.cos(destination_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(haversine))


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
