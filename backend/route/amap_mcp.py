"""Amap MCP route and distance adapters."""

import math
from collections.abc import Mapping

from backend.amap_mcp import (
    AmapMcpAPIError,
    AmapMcpClient,
    AmapMcpConfigurationError,
)
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
        origin: AttractionStop,
        destination: AttractionStop,
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
                    return RouteSegment.model_validate(cached)
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

        distance_m, duration_seconds = _route_values(payload, mode)
        result = RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            origin_longitude=origin.longitude,
            origin_latitude=origin.latitude,
            destination_longitude=destination.longitude,
            destination_latitude=destination.latitude,
            distance_m=distance_m,
            duration_min=max(1, math.ceil(duration_seconds / 60)),
            mode=mode,
            source="amap_mcp",
        )
        if self._cache is not None:
            await self._cache.set_json(
                cache_key,
                result.model_dump(mode="json"),
                ttl_seconds=self._cache_ttl_seconds,
            )
        return result

    async def get_distance(self, origin: AttractionStop, destination: AttractionStop) -> int:
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


def _require_coordinates(origin: AttractionStop, destination: AttractionStop) -> None:
    if None in (
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    ):
        raise RouteAPIError("路线起点或终点缺少坐标")


def _coordinate(longitude: float, latitude: float) -> str:
    return f"{longitude:.6f},{latitude:.6f}"


def _route_values(payload: Mapping[str, object], mode: RouteMode) -> tuple[int, int]:
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
    return round(distance_m), round(duration_seconds)


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
