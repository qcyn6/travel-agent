"""Resolve display-only attraction photos after final itinerary selection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

import httpx

from backend.models.retrieval import HybridSearchResult
from backend.observability import observe_external_request


logger = logging.getLogger(__name__)


class AmapAttractionPhotoService:
    """Fetch one Amap POI photo for each final attraction.

    Photos are transient presentation data. The service never writes to the
    attraction catalog, and cache entries include negative lookups so opening
    a saved plan repeatedly does not spend more POI quota.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 8.0,
        max_concurrency: int = 3,
        cache=None,
        cache_ttl_seconds: int = 604800,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max(1, int(max_concurrency))
        self.cache = cache
        self.cache_ttl_seconds = max(3600, int(cache_ttl_seconds))
        self.client = client

    async def enrich(
        self,
        *,
        city: str,
        candidates: Sequence[HybridSearchResult],
    ) -> dict[str, str]:
        if not self.api_key or not candidates:
            return {}
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def resolve(candidate: HybridSearchResult) -> tuple[str, str | None]:
            async with semaphore:
                return candidate.poi_id, await self._resolve_one(city, candidate)

        results = await asyncio.gather(*(resolve(item) for item in candidates))
        return {poi_id: url for poi_id, url in results if url}

    async def _resolve_one(
        self,
        city: str,
        candidate: HybridSearchResult,
    ) -> str | None:
        cache_key = f"attraction-photo:{city}:{_normalize_name(candidate.name)}"
        if self.cache is not None:
            cached = await self.cache.get_json(cache_key)
            if isinstance(cached, Mapping) and "url" in cached:
                return _safe_photo_url(cached.get("url"))

        url = None
        lookup_succeeded = False
        try:
            payload = await self._search(city, candidate.name)
            lookup_succeeded = True
            pois = payload.get("pois")
            if isinstance(pois, list):
                expected = _normalize_name(candidate.name)
                exact = next(
                    (
                        item
                        for item in pois
                        if isinstance(item, Mapping)
                        and _normalize_name(str(item.get("name") or "")) == expected
                    ),
                    None,
                )
                if isinstance(exact, Mapping):
                    url = _first_photo_url(exact.get("photos"))
        except Exception as exc:
            logger.warning(
                "高德景点图片查询失败，使用占位图：poi=%s error=%s",
                candidate.poi_id,
                type(exc).__name__,
            )

        if self.cache is not None and lookup_succeeded:
            await self.cache.set_json(
                cache_key,
                {"url": url},
                ttl_seconds=self.cache_ttl_seconds,
            )
        return url

    async def _search(self, city: str, name: str) -> dict:
        params = {
            "key": self.api_key,
            "keywords": name,
            "region": city,
            "city_limit": "true",
            "show_fields": "photos",
            "page_size": 5,
            "page_num": 1,
        }
        async with observe_external_request("amap", "attraction_photo"):
            if self.client is not None:
                response = await self.client.get(
                    f"{self.base_url}/v5/place/text",
                    params=params,
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(
                        f"{self.base_url}/v5/place/text",
                        params=params,
                    )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise ValueError("高德景点图片响应不可用")
        return payload


def build_attraction_photo_service(settings, *, cache=None):
    if not getattr(settings, "amap_api_key", None):
        return None
    return AmapAttractionPhotoService(
        settings.amap_api_key,
        timeout_seconds=settings.amap_timeout_seconds,
        max_concurrency=3,
        cache=cache,
        # POI photos change infrequently. Keep them longer than ordinary POI
        # search results without introducing another database/config field.
        cache_ttl_seconds=max(604800, settings.redis_poi_ttl_seconds),
    )


def _first_photo_url(value: object) -> str | None:
    if isinstance(value, Mapping):
        return _safe_photo_url(value.get("url"))
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, Mapping):
            url = _safe_photo_url(item.get("url"))
            if url:
                return url
    return None


def _safe_photo_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    parsed = urlparse(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _normalize_name(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


__all__ = ["AmapAttractionPhotoService", "build_attraction_photo_service"]
