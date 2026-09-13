import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from backend.models.attraction import AttractionPOI


class AmapConfigurationError(RuntimeError):
    pass


class AmapAPIError(RuntimeError):
    def __init__(self, message: str, *, infocode: str | None = None):
        super().__init__(message)
        self.infocode = infocode


_NON_ATTRACTION_NAME_PARTS = (
    "停车场",
    "售票处",
    "卫生间",
    "游客中心",
    "服务中心",
    "出入口",
    "入口",
    "出口",
)


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", []) else None
    except (TypeError, ValueError):
        return None


def _coordinates(value: Any) -> tuple[float | None, float | None]:
    if not isinstance(value, str):
        return None, None
    try:
        longitude, latitude = value.split(",", 1)
        return float(longitude), float(latitude)
    except (TypeError, ValueError):
        return None, None


def _to_attraction(raw: Mapping[str, Any], requested_city: str) -> AttractionPOI | None:
    poi_id = _optional_text(raw.get("id"))
    name = _optional_text(raw.get("name"))
    if not poi_id or not name or any(part in name for part in _NON_ATTRACTION_NAME_PARTS):
        return None

    longitude, latitude = _coordinates(raw.get("location"))
    business = raw.get("business") if isinstance(raw.get("business"), Mapping) else {}
    city = _optional_text(raw.get("cityname")) or requested_city
    return AttractionPOI(
        poi_id=poi_id,
        name=name,
        city=city,
        category=_optional_text(raw.get("type")),
        address=_optional_text(raw.get("address")),
        longitude=longitude,
        latitude=latitude,
        rating=_optional_float(business.get("rating")),
    )


def clean_attractions(raw_pois: list[Mapping[str, Any]], city: str) -> list[AttractionPOI]:
    result: list[AttractionPOI] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in raw_pois:
        attraction = _to_attraction(raw, city)
        if attraction is None:
            continue
        normalized_name = "".join(attraction.name.split()).casefold()
        if attraction.poi_id in seen_ids or normalized_name in seen_names:
            continue
        seen_ids.add(attraction.poi_id)
        seen_names.add(normalized_name)
        result.append(attraction)
    return result


class AmapAttractionClient:
    """Minimal async adapter for AMap Place Search 2.0."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 8.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
        cache=None,
        cache_ttl_seconds: int = 86400,
    ):
        if not api_key:
            raise AmapConfigurationError("未配置 AMAP_API_KEY")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]:
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间")

        cache_key = f"poi:{city}:{limit}"
        if self._cache is not None:
            cached = await self._cache.get_json(cache_key)
            if isinstance(cached, list):
                try:
                    return [AttractionPOI.model_validate(item) for item in cached]
                except (TypeError, ValueError):
                    pass

        raw_pois: list[Mapping[str, Any]] = []
        page_count = (limit + 24) // 25
        async with self._managed_client() as client:
            for page_num in range(1, page_count + 1):
                payload = await self._request_page(client, city=city, page_num=page_num)
                page_pois = payload.get("pois", [])
                if not isinstance(page_pois, list):
                    raise AmapAPIError("高德 POI 返回格式异常")
                raw_pois.extend(item for item in page_pois if isinstance(item, Mapping))
                if len(page_pois) < 25:
                    break

        attractions = clean_attractions(raw_pois, city)[:limit]
        if self._cache is not None:
            await self._cache.set_json(
                cache_key,
                [item.model_dump(mode="json") for item in attractions],
                ttl_seconds=self._cache_ttl_seconds,
            )
        return attractions

    def _managed_client(self):
        if self._client is not None:
            return _BorrowedAsyncClient(self._client)
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    async def _request_page(self, client: httpx.AsyncClient, *, city: str, page_num: int) -> dict[str, Any]:
        params = {
            "key": self._api_key,
            "types": "风景名胜",
            "region": city,
            "city_limit": "true",
            "show_fields": "business",
            "page_size": 25,
            "page_num": page_num,
        }
        for attempt in range(self._max_retries):
            try:
                response = await client.get(f"{self._base_url}/v5/place/text", params=params)
            except httpx.RequestError as exc:
                if attempt + 1 == self._max_retries:
                    raise AmapAPIError("高德 POI 服务网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue

            if response.status_code >= 500:
                if attempt + 1 == self._max_retries:
                    raise AmapAPIError("高德 POI 服务暂时不可用")
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if response.status_code != 200:
                raise AmapAPIError(f"高德 POI 服务返回 HTTP {response.status_code}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise AmapAPIError("高德 POI 返回了无效 JSON") from exc
            if not isinstance(payload, dict):
                raise AmapAPIError("高德 POI 返回格式异常")
            if payload.get("status") != "1":
                info = _optional_text(payload.get("info")) or "未知错误"
                infocode = _optional_text(payload.get("infocode"))
                raise AmapAPIError(f"高德 POI 请求失败：{info}", infocode=infocode)
            return payload
        raise AmapAPIError("高德 POI 服务请求失败")


class _BorrowedAsyncClient:
    """Use an injected client without taking ownership of its lifecycle."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, *_: object) -> None:
        return None
