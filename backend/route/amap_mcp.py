"""Amap MCP route and distance adapters."""

import math
from collections.abc import Mapping

from backend.amap_mcp import (
    AmapMcpAPIError,
    AmapMcpClient,
    AmapMcpConfigurationError,
)
from backend.models.hotel import HotelStay
from backend.models.route import RouteMode, RouteSegment
from backend.models.travel_plan import AttractionStop


class RouteConfigurationError(RuntimeError):
    """高德 MCP 路线服务配置错误。"""


class RouteAPIError(RuntimeError):
    """高德 MCP 路线服务调用或响应错误。"""


class AmapMcpRouteClient:
    """Use Amap MCP for walking, driving, transit and straight-line distance."""

    _TOOLS = {
        "walking": "maps_direction_walking",
        "driving": "maps_direction_driving",
        "transit": "maps_direction_transit_integrated",
    }

    def __init__(
        self,
        api_key: str | None,
        *,
        endpoint: str = "https://mcp.amap.com/mcp",
        timeout_seconds: float = 15.0,
        sse_read_timeout_seconds: float = 20.0,
        cache=None,
        cache_ttl_seconds: int = 10800,
        mcp_client: AmapMcpClient | None = None,
    ):
        try:
            self._mcp = mcp_client or AmapMcpClient(
                api_key,
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
                sse_read_timeout_seconds=sse_read_timeout_seconds,
            )
        except AmapMcpConfigurationError as exc:
            raise RouteConfigurationError(str(exc)) from exc
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def get_route(
        self,
        origin: AttractionStop | HotelStay,
        destination: AttractionStop | HotelStay,
        *,
        mode: RouteMode,
        city_adcode: str,
    ) -> RouteSegment:
        del city_adcode
        _require_coordinates(origin, destination)
        cache_key = f"route:mcp:{origin.poi_id}:{destination.poi_id}:{mode}"
        if self._cache is not None:
            cached = await self._cache.get_json(cache_key)
            if isinstance(cached, dict):
                try:
                    cached_segment = RouteSegment.model_validate(cached)
                    return cached_segment.model_copy(update={
                        "origin_city": origin.city,
                        "destination_city": destination.city,
                    })
                except (TypeError, ValueError):
                    pass

        arguments = {
            "origin": _coordinate(origin.longitude, origin.latitude),
            "destination": _coordinate(destination.longitude, destination.latitude),
        }
        if mode == "transit":
            arguments.update(
                {
                    "city": origin.city,
                    "cityd": destination.city,
                }
            )
        try:
            payload = await self._mcp.call_tool(
                self._TOOLS[mode],
                arguments,
                operation="route",
            )
        except AmapMcpConfigurationError as exc:
            raise RouteConfigurationError(str(exc)) from exc
        except AmapMcpAPIError as exc:
            raise RouteAPIError("高德 MCP 路线服务不可用") from exc

        distance_m, duration_seconds, polyline = _route_values(payload, mode)
        result = RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            origin_longitude=origin.longitude,
            origin_latitude=origin.latitude,
            destination_longitude=destination.longitude,
            destination_latitude=destination.latitude,
            origin_city=origin.city,
            destination_city=destination.city,
            distance_m=distance_m,
            duration_min=max(1, math.ceil(duration_seconds / 60)),
            mode=mode,
            source="amap_mcp",
            polyline=polyline,
        )
        if self._cache is not None:
            await self._cache.set_json(
                cache_key,
                result.model_dump(mode="json"),
                ttl_seconds=self._cache_ttl_seconds,
            )
        return result

    async def get_distance(self, origin: AttractionStop | HotelStay, destination: AttractionStop | HotelStay) -> int:
        """Return the MCP straight-line distance used by automatic mode choice."""

        _require_coordinates(origin, destination)
        cache_key = f"distance:mcp:{origin.poi_id}:{destination.poi_id}"
        if self._cache is not None:
            cached = await self._cache.get_json(cache_key)
            try:
                if cached is not None:
                    return int(cached)
            except (TypeError, ValueError):
                pass
        try:
            payload = await self._mcp.call_tool(
                "maps_distance",
                {
                    "origins": _coordinate(origin.longitude, origin.latitude),
                    "destination": _coordinate(destination.longitude, destination.latitude),
                    "type": "0",
                },
                operation="distance",
            )
        except AmapMcpConfigurationError as exc:
            raise RouteConfigurationError(str(exc)) from exc
        except AmapMcpAPIError as exc:
            raise RouteAPIError("高德 MCP 距离服务不可用") from exc

        distance_m = _distance_value(payload)
        if self._cache is not None:
            await self._cache.set_json(
                cache_key,
                distance_m,
                ttl_seconds=self._cache_ttl_seconds,
            )
        return distance_m


def _require_coordinates(origin: AttractionStop | HotelStay, destination: AttractionStop | HotelStay) -> None:
    if None in (
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    ):
        raise RouteAPIError("路线起点或终点缺少坐标")


def _coordinate(longitude: float, latitude: float) -> str:
    return f"{longitude:.6f},{latitude:.6f}"


def _route_values(
    payload: Mapping[str, object],
    mode: RouteMode,
) -> tuple[int, int, list[tuple[float, float]]]:
    root = payload.get("route")
    if not isinstance(root, Mapping):
        root = payload
    collection_name = "transits" if mode == "transit" else "paths"
    choices = root.get(collection_name)
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RouteAPIError("高德 MCP 路线结果没有可用方案")
    choice = choices[0]
    cost = choice.get("cost") if isinstance(choice.get("cost"), Mapping) else {}
    distance_m = _number(choice.get("distance"))
    if distance_m is None:
        distance_m = _number(root.get("distance"))
    duration_seconds = _number(choice.get("duration"))
    if duration_seconds is None:
        duration_seconds = _number(cost.get("duration"))
    if distance_m is None or duration_seconds is None:
        raise RouteAPIError("高德 MCP 路线结果缺少距离或耗时")
    return round(distance_m), round(duration_seconds), _route_polyline(choice)


def _route_polyline(value: object) -> list[tuple[float, float]]:
    """Collect provider step polylines in response order.

    Walking and driving responses expose ``steps[].polyline`` while transit
    responses may nest walking and bus sections several levels deeper. A
    recursive collector keeps the adapter compatible with both shapes without
    retaining the complete provider payload in saved plans.
    """

    raw_polylines: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            polyline = item.get("polyline")
            if isinstance(polyline, str) and polyline.strip():
                raw_polylines.append(polyline)
            for key, child in item.items():
                if key != "polyline":
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    points: list[tuple[float, float]] = []
    for raw in raw_polylines:
        for pair in raw.split(";"):
            try:
                longitude_text, latitude_text = pair.split(",", 1)
                point = (float(longitude_text), float(latitude_text))
            except (TypeError, ValueError):
                continue
            if not points or point != points[-1]:
                points.append(point)
    return points


def _distance_value(payload: Mapping[str, object]) -> int:
    results = payload.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
        raise RouteAPIError("高德 MCP 距离结果没有可用数据")
    distance_m = _number(results[0].get("distance"))
    if distance_m is None:
        raise RouteAPIError("高德 MCP 距离结果缺少距离")
    return round(distance_m)


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "AmapMcpRouteClient",
    "RouteAPIError",
    "RouteConfigurationError",
]
