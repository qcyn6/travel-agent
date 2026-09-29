"""Find an overnight hotel after each day's final attraction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx

from backend.models.hotel import HotelStay
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan
from backend.observability import observe_external_request
from backend.schedule.opening_hours import visit_fits_opening_hours

logger = logging.getLogger(__name__)


class AmapHotelSearchService:
    """Search nearby hotel POIs and retain their display photo when available."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 8.0,
        max_retries: int = 3,
        cache=None,
        cache_ttl_seconds: int = 86400,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.cache = cache
        self.cache_ttl_seconds = max(3600, int(cache_ttl_seconds))
        self.client = client

    async def search(
        self,
        *,
        city: str,
        origin: AttractionStop,
        limit: int = 8,
    ) -> list[HotelStay]:
        if not self.api_key or origin.longitude is None or origin.latitude is None:
            return []
        limit = max(1, min(int(limit), 25))
        cache_key = (
            f"hotel:{city}:{origin.poi_id}:"
            f"{origin.longitude:.5f},{origin.latitude:.5f}:{limit}"
        )
        if self.cache is not None:
            cached = await self.cache.get_json(cache_key)
            # An empty cached response may be a transient provider result. Do
            # not let it suppress a later successful lookup for a full day.
            if isinstance(cached, list) and cached:
                try:
                    return [HotelStay.model_validate(item) for item in cached]
                except (TypeError, ValueError):
                    pass

        hotels: list[HotelStay] = []
        for attempt in range(self.max_retries):
            try:
                payload = await self._search(city=city, origin=origin, limit=limit)
                raw_pois = payload.get("pois")
                if isinstance(raw_pois, list):
                    hotels = _clean_hotels(raw_pois, city)[:limit]
                # A successful HTTP response with no usable hotel POI is also
                # retried because Amap can return an empty page transiently
                # under concurrent requests or provider rate limiting.
                if hotels or attempt + 1 >= self.max_retries:
                    break
                logger.warning(
                    "高德附近酒店查询返回空结果，准备重试：city=%s attempt=%s/%s",
                    city,
                    attempt + 1,
                    self.max_retries,
                )
            except Exception as exc:
                if attempt + 1 >= self.max_retries:
                    logger.warning(
                        "高德附近酒店查询失败，继续生成无酒店方案：city=%s attempts=%s error=%s",
                        city,
                        self.max_retries,
                        type(exc).__name__,
                    )
                    break
                logger.warning(
                    "高德附近酒店查询失败，准备重试：city=%s attempt=%s/%s error=%s",
                    city,
                    attempt + 1,
                    self.max_retries,
                    type(exc).__name__,
                )
            await asyncio.sleep(0.25 * (2**attempt))

        # Only positive results are cached. Empty results should be retried on
        # the next planning request instead of poisoning the key for 24 hours.
        if self.cache is not None and hotels:
            await self.cache.set_json(
                cache_key,
                [hotel.model_dump(mode="json") for hotel in hotels],
                ttl_seconds=self.cache_ttl_seconds,
            )
        return hotels

    async def _search(
        self,
        *,
        city: str,
        origin: AttractionStop,
        limit: int,
    ) -> dict:
        params = {
            "key": self.api_key,
            "keywords": "酒店",
            "region": city,
            "city_limit": "true",
            "location": f"{origin.longitude:.6f},{origin.latitude:.6f}",
            "radius": 10000,
            "sortrule": "distance",
            "show_fields": "business,photos",
            "page_size": limit,
            "page_num": 1,
        }
        async with observe_external_request("amap", "hotel_search"):
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
            raise ValueError("高德酒店 POI 响应不可用")
        return payload


class HotelEnrichmentService:
    """Attach overnight hotels and their inbound/outbound routes to each day.

    The current night's hotel route is written to ``TravelDay.hotel_route``
    and remains outside today's time window. The previous night's hotel route
    is written to ``TravelDay.start_hotel_route`` and is counted by the
    schedule builder before today's first attraction.
    """

    def __init__(
        self,
        searcher,
        route_service,
        *,
        max_candidates: int = 5,
        max_concurrency: int = 2,
    ):
        self.searcher = searcher
        self.route_service = route_service
        self.max_candidates = max(1, int(max_candidates))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def enrich_plan(self, plan: TravelPlan, *, requested_mode="auto") -> TravelPlan:
        days = await asyncio.gather(
            *(
                self.enrich_day(day, requested_mode=requested_mode)
                for day in plan.itinerary
            )
        )
        return plan.model_copy(update={"itinerary": days})

    async def enrich_day(self, day: TravelDay, *, requested_mode="auto") -> TravelDay:
        async with self._semaphore:
            return await self._enrich_day(day, requested_mode=requested_mode)

    async def connect_overnight(
        self,
        previous_hotel: HotelStay,
        day: TravelDay,
        *,
        requested_mode="auto",
        daily_start_time: str | None = None,
        daily_end_time: str | None = None,
        meal_minutes: int = 0,
    ) -> TravelDay:
        """Connect the previous night's hotel to a feasible first stop today.

        The transfer starts at the user's day-start time and is counted inside
        today's time window. If that arrival makes the current first stop
        unavailable, try the following planned stops directly; never wait for
        an opening interval.
        """

        if not day.attractions or self.route_service is None:
            return day.model_copy(update={"start_hotel": previous_hotel})

        window_start = _parse_clock(daily_start_time or "08:00")
        window_end = _parse_clock(daily_end_time or "21:00")
        fallback: tuple[int, RouteSegment] | None = None
        selected: tuple[int, RouteSegment] | None = None
        for index, attraction in enumerate(day.attractions):
            try:
                route = await self.route_service.resolve_segment(
                    previous_hotel,
                    attraction,
                    requested_mode=requested_mode,
                    city_adcode=day.city_adcode,
                )
            except Exception as exc:
                logger.warning(
                    "次日酒店到首站路线查询失败：hotel=%s poi=%s error=%s",
                    previous_hotel.poi_id,
                    attraction.poi_id,
                    type(exc).__name__,
                )
                continue
            if route is None:
                continue
            if fallback is None:
                fallback = (index, route)
            if route.duration_min is None:
                continue

            arrival = window_start + route.duration_min
            if meal_minutes > 0 and arrival >= 12 * 60 and arrival + meal_minutes <= window_end:
                arrival += meal_minutes
            visit_minutes = attraction.visit_duration_minutes
            if arrival + visit_minutes > window_end:
                continue
            if not visit_fits_opening_hours(
                attraction.opening_hours_weekly,
                day.date,
                arrival,
                visit_minutes,
            ):
                continue
            selected = (index, route)
            break

        if selected is None:
            selected = fallback
        if selected is None:
            # Keep the overnight hotel visible when the provider cannot return
            # any route. The schedule remains explicitly route-incomplete.
            return day.model_copy(update={"start_hotel": previous_hotel})

        index, route = selected
        return day.model_copy(
            update={
                "start_hotel": previous_hotel,
                "start_hotel_route": route,
                "attractions": day.attractions[index:],
                "routes": day.routes[index:],
            }
        )

    async def _enrich_day(self, day: TravelDay, *, requested_mode="auto") -> TravelDay:
        if self.searcher is None or not day.attractions:
            return day
        origin = day.attractions[-1]
        candidates = await self.searcher.search(
            city=day.city,
            origin=origin,
            limit=self.max_candidates,
        )
        if not candidates:
            return day

        async def resolve(hotel: HotelStay):
            if self.route_service is None:
                return hotel, None
            try:
                route = await self.route_service.resolve_segment(
                    origin,
                    hotel,
                    requested_mode=requested_mode,
                    city_adcode=day.city_adcode,
                )
            except Exception as exc:
                logger.warning(
                    "酒店路线查询失败，保留酒店但不显示路线：hotel=%s error=%s",
                    hotel.poi_id,
                    type(exc).__name__,
                )
                route = None
            return hotel, route

        resolved = await asyncio.gather(*(resolve(hotel) for hotel in candidates))
        hotel, route = min(
            resolved,
            key=lambda item: _hotel_route_sort_key(item[1]),
        )
        return day.model_copy(update={"hotel": hotel, "hotel_route": route})


def build_hotel_enrichment_service(settings, *, route_service, cache=None):
    api_key = getattr(settings, "amap_api_key", None)
    if not api_key:
        return None
    searcher = AmapHotelSearchService(
        api_key,
        timeout_seconds=getattr(settings, "amap_timeout_seconds", 8.0),
        max_retries=getattr(settings, "amap_max_retries", 3),
        cache=cache,
        cache_ttl_seconds=max(86400, getattr(settings, "redis_poi_ttl_seconds", 86400)),
    )
    return HotelEnrichmentService(
        searcher,
        route_service,
        max_concurrency=getattr(settings, "hotel_enrichment_concurrency", 2),
    )


def _hotel_route_sort_key(route) -> tuple[int, int, int, int]:
    """Choose the physically nearest candidate, preferring real data on ties."""

    if route is None:
        return (10**12, 1, 10**12, 1)
    real_rank = 0 if route.source in {"amap_mcp", "amap"} and not route.estimated else 1
    distance = route.distance_m if route.distance_m is not None else 10**12
    duration = route.duration_min if route.duration_min is not None else 10**12
    unavailable_rank = 1 if route.source == "unavailable" else 0
    return distance, real_rank, duration, unavailable_rank


def _clean_hotels(raw_pois: list[object], requested_city: str) -> list[HotelStay]:
    hotels: list[HotelStay] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in raw_pois:
        if not isinstance(raw, Mapping):
            continue
        poi_id = _optional_text(raw.get("id"))
        name = _optional_text(raw.get("name"))
        if not poi_id or not name:
            continue
        normalized_name = "".join(name.split()).casefold()
        if poi_id in seen_ids or normalized_name in seen_names:
            continue
        longitude, latitude = _coordinates(raw.get("location"))
        business = raw.get("business") if isinstance(raw.get("business"), Mapping) else {}
        hotel = HotelStay(
            poi_id=poi_id,
            name=name,
            city=_optional_text(raw.get("cityname")) or requested_city,
            address=_optional_text(raw.get("address")),
            rating=_optional_float(business.get("rating")),
            longitude=longitude,
            latitude=latitude,
            image_url=_first_photo_url(raw.get("photos")),
            source="amap",
        )
        seen_ids.add(poi_id)
        seen_names.add(normalized_name)
        hotels.append(hotel)
    return hotels


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "", []) else None
    except (TypeError, ValueError):
        return None


def _coordinates(value: object) -> tuple[float | None, float | None]:
    if not isinstance(value, str):
        return None, None
    try:
        longitude, latitude = value.split(",", 1)
        return float(longitude), float(latitude)
    except (TypeError, ValueError):
        return None, None


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
    url = value.strip()
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _parse_clock(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("时间必须使用 HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间必须使用 HH:MM")
    return hour * 60 + minute


__all__ = [
    "AmapHotelSearchService",
    "HotelEnrichmentService",
    "build_hotel_enrichment_service",
]
