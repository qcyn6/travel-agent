import asyncio
import math
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

from backend.models.route import RouteMode, RouteSegment
from backend.models.travel_plan import AttractionStop


class RouteConfigurationError(RuntimeError):
    pass


class RouteAPIError(RuntimeError):
    pass


class AmapRouteClient:
    """高德路径规划 2.0 的最小异步适配器。"""

    _ENDPOINTS = {
        "walking": "/v5/direction/walking",
        "transit": "/v5/direction/transit/integrated",
        "driving": "/v5/direction/driving",
    }

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 8.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def get_route(
        self,
        origin: AttractionStop,
        destination: AttractionStop,
        *,
        mode: RouteMode,
        city_adcode: str,
    ) -> RouteSegment:
        if not self.api_key:
            raise RouteConfigurationError("未配置 AMAP_API_KEY")
        if None in (
            origin.longitude,
            origin.latitude,
            destination.longitude,
            destination.latitude,
        ):
            raise RouteAPIError("路线起点或终点缺少坐标")

        params = {
            "key": self.api_key,
            "origin": _coordinate(origin.longitude, origin.latitude),
            "destination": _coordinate(destination.longitude, destination.latitude),
            "show_fields": "cost",
        }
        if mode == "transit":
            params.update(
                {
                    "originpoi": origin.poi_id,
                    "destinationpoi": destination.poi_id,
                    "city1": city_adcode,
                    "city2": city_adcode,
                    "strategy": "0",
                }
            )
        else:
            params.update(
                {
                    "origin_id": origin.poi_id,
                    "destination_id": destination.poi_id,
                }
            )
            if mode == "driving":
                params["strategy"] = "32"

        payload = await self._request(self._ENDPOINTS[mode], params)
        distance_m, duration_seconds = _route_values(payload, mode)
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
            duration_min=max(1, math.ceil(duration_seconds / 60)),
            mode=mode,
            source="amap",
        )

    async def _request(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        async with self._managed_client() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.get(f"{self.base_url}{endpoint}", params=params)
                except httpx.RequestError as exc:
                    if attempt + 1 == self.max_retries:
                        raise RouteAPIError("高德路线服务网络请求失败") from exc
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                if response.status_code != 200:
                    raise RouteAPIError(f"高德路线服务返回 HTTP {response.status_code}")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise RouteAPIError("高德路线服务返回了无效 JSON") from exc
                if not isinstance(payload, dict) or payload.get("status") != "1":
                    raise RouteAPIError("高德路线服务返回失败状态")
                return payload
        raise RouteAPIError("高德路线服务请求失败")

    def _managed_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        if self.client is not None:
            return _BorrowedAsyncClient(self.client)
        return httpx.AsyncClient(timeout=self.timeout_seconds)


class _BorrowedAsyncClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, *_: object) -> None:
        return None


def _coordinate(longitude: float, latitude: float) -> str:
    return f"{longitude:.6f},{latitude:.6f}"


def _route_values(payload: dict[str, Any], mode: RouteMode) -> tuple[int, int]:
    route = payload.get("route")
    if not isinstance(route, dict):
        raise RouteAPIError("高德路线结果缺少 route")
    collection_name = "transits" if mode == "transit" else "paths"
    choices = route.get(collection_name)
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RouteAPIError("高德路线结果没有可用方案")
    choice = choices[0]
    cost = choice.get("cost") if isinstance(choice.get("cost"), dict) else {}
    try:
        distance_m = int(float(choice["distance"]))
        duration_seconds = int(float(cost.get("duration") or choice["duration"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RouteAPIError("高德路线结果缺少距离或耗时") from exc
    return distance_m, duration_seconds
