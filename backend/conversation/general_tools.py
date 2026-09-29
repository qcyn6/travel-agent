from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from datetime import date, timedelta

from backend.amap_mcp import AmapMcpAPIError, AmapMcpClient, AmapMcpConfigurationError
from backend.cache import build_cache
from backend.models.general_chat import GeneralIntentDecision
from backend.models.travel_plan import AttractionStop
from backend.requirements.dates import format_date_zh
from backend.route import AmapMcpRouteClient, RouteConfigurationError, RouteEnrichmentService
from backend.route.amap_mcp import RouteAPIError
from backend.weather import QWeatherAPIError, QWeatherClient, QWeatherConfigurationError


logger = logging.getLogger(__name__)


class AmapMcpPlaceResolver:
    """Resolve a free-form place name to coordinates through Amap MCP."""

    def __init__(self, client: AmapMcpClient, *, detail_concurrency: int = 5):
        self.client = client
        self.detail_concurrency = max(1, detail_concurrency)

    async def resolve(self, name: str, *, city: str | None = None) -> AttractionStop | None:
        arguments = {"address": name}
        if city:
            arguments["city"] = city
        payload = None
        for attempt in range(2):
            try:
                payload = await self.client.call_tool("maps_geo", arguments, operation="geocode")
                break
            except (AmapMcpAPIError, AmapMcpConfigurationError):
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                return None
        # The official MCP adapter currently returns ``results`` while some
        # older Amap-compatible adapters return ``geocodes``. Accept both
        # shapes so free-form endpoints such as railway stations can still be
        # resolved when they are not present in the local attraction table.
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            geocodes = payload.get("results")
        if not isinstance(geocodes, list) or not geocodes:
            return None
        item = geocodes[0]
        if not isinstance(item, Mapping):
            return None
        coordinates = _coordinates(item.get("location"))
        if coordinates is None:
            return None
        longitude, latitude = coordinates
        resolved_city = (
            _text(item.get("city"))
            or _text(item.get("cityname"))
            or city
            or _text(item.get("province"))
            or "未知城市"
        )
        return AttractionStop(
            poi_id=f"general:{name}",
            name=(
                _text(item.get("formatted_address"))
                or _text(item.get("name"))
                or _text(item.get("address"))
                or name
            ),
            city=resolved_city,
            longitude=longitude,
            latitude=latitude,
        )

    async def search_nearby(
        self,
        *,
        keywords: str,
        location: str,
        radius_m: int = 2000,
        limit: int = 6,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        center = _coordinates(location)
        async with self.client.session_scope():
            payload = await self.client.call_tool(
                "maps_around_search",
                {
                    "keywords": keywords,
                    "location": location,
                    "radius": str(radius_m),
                },
                operation="nearby_poi_search",
            )
            raw_pois = payload.get("pois")
            if not isinstance(raw_pois, list):
                nested = payload.get("data")
                raw_pois = nested.get("pois") if isinstance(nested, Mapping) else None
            if not isinstance(raw_pois, list):
                return []

            semaphore = asyncio.Semaphore(self.detail_concurrency)

            async def resolve(raw: object) -> dict[str, object] | None:
                if not isinstance(raw, Mapping):
                    return None
                poi_id = _text(raw.get("id") or raw.get("poiid"))
                detail: Mapping[str, object] = raw
                coordinates = _valid_coordinates(_coordinates(raw.get("location")))
                if poi_id and coordinates is None:
                    async with semaphore:
                        try:
                            detail_payload = await self.client.call_tool(
                                "maps_search_detail",
                                {"id": poi_id},
                                operation="nearby_poi_detail",
                            )
                            detail = _detail_record(detail_payload)
                        except Exception as exc:  # noqa: BLE001 - keep usable search candidates
                            logger.warning(
                                "普通模式附近地点详情获取失败：poi=%s error=%s",
                                poi_id,
                                type(exc).__name__,
                            )
                coordinates = _valid_coordinates(
                    _coordinates(detail.get("location"))
                ) or coordinates
                name = _text(detail.get("name")) or _text(raw.get("name"))
                if not name or coordinates is None:
                    return None
                longitude, latitude = coordinates
                distance_m = _number_or_none(
                    detail.get("distance") or raw.get("distance")
                )
                if distance_m is None and center is not None:
                    distance_m = _straight_line_distance_m(center, coordinates)
                if distance_m is not None:
                    distance_m = round(distance_m)
                    if distance_m > radius_m:
                        return None
                return {
                    "poi_id": _text(detail.get("id")) or poi_id or name,
                    "name": name,
                    "address": _text(detail.get("address")) or _text(raw.get("address")),
                    "category": (
                        _text(detail.get("type"))
                        or _text(raw.get("type"))
                        or _text(raw.get("typecode"))
                    ),
                    "longitude": longitude,
                    "latitude": latitude,
                    "distance_m": distance_m,
                }

            # Amap's nearby-search results provide POI IDs but usually omit
            # coordinates. Fetch details in bounded batches and stop as soon
            # as enough mappable POIs have been resolved.
            places: list[dict[str, object]] = []
            seen: set[str] = set()
            candidate_limit = min(len(raw_pois), limit * 2)
            for start in range(0, candidate_limit, limit):
                candidates = raw_pois[start : start + limit]
                resolved = await asyncio.gather(*(resolve(raw) for raw in candidates))
                for place in resolved:
                    if place is None:
                        continue
                    poi_id = str(place["poi_id"])
                    if poi_id in seen:
                        continue
                    seen.add(poi_id)
                    places.append(place)
                    if len(places) >= limit:
                        break
                if len(places) >= limit:
                    break
        return places


class GeneralTravelToolService:
    """Execute bounded route, weather and nearby-place queries for ordinary chat."""

    def __init__(
        self,
        *,
        catalog_repository,
        attraction_repository,
        route_service: RouteEnrichmentService | None = None,
        place_resolver: AmapMcpPlaceResolver | None = None,
        weather_client=None,
        today_provider=date.today,
    ):
        self.catalog_repository = catalog_repository
        self.attraction_repository = attraction_repository
        self.route_service = route_service
        self.place_resolver = place_resolver
        self.weather_client = weather_client
        self.today_provider = today_provider
        self.last_card: dict[str, object] | None = None
        self.last_tool_result: dict[str, object] | None = None

    def _record_result(
        self,
        status: str,
        message: str,
        *,
        data: object = None,
        retryable: bool = False,
        missing_fields: list[str] | None = None,
    ) -> str:
        """Keep the human-readable response and expose a safe Agent envelope."""

        self.last_tool_result = {
            "status": status,
            "message": message,
            "data": data,
            "retryable": retryable,
            "missing_fields": missing_fields or [],
        }
        return message

    async def execute(self, decision: GeneralIntentDecision) -> str | None:
        self.last_card = None
        if decision.includes("nearby"):
            return await self.query_nearby(decision)
        if decision.includes("route"):
            return await self.query_route(decision)
        if decision.includes("weather"):
            return await self.query_weather(decision)
        return None

    async def query_nearby(self, decision: GeneralIntentDecision) -> str:
        self.last_card = None
        self.last_tool_result = None
        if "nearby_center" in decision.missing_fields or not decision.nearby_center:
            return self._record_result(
                "needs_clarification",
                "请告诉我你想以哪个具体地点为中心搜索附近服务。",
                missing_fields=["center"],
            )
        if "nearby_category" in decision.missing_fields or not decision.nearby_category:
            return self._record_result(
                "needs_clarification",
                f"请告诉我想在{decision.nearby_center}附近找什么，例如厕所、停车场或餐饮。",
                missing_fields=["category"],
            )
        if self.catalog_repository.find_city(decision.nearby_center) is not None:
            return self._record_result(
                "needs_clarification",
                f"“{decision.nearby_center}”范围比较大，你想以哪个具体地点为中心找"
                f"{decision.nearby_category}？可以告诉我景点、车站或商圈。",
                missing_fields=["specific_center"],
            )
        try:
            center = await self._resolve_place(decision.nearby_center)
        except Exception as exc:  # noqa: BLE001 - return a safe status to the Agent
            logger.warning("普通模式附近地点解析失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error",
                "附近地点解析暂时失败，请稍后重试。",
                retryable=True,
            )
        if center is None or center.longitude is None or center.latitude is None:
            return self._record_result(
                "needs_clarification",
                f"暂时无法确认“{decision.nearby_center}”的位置，请补充更具体的景点、车站或商圈名称。",
                missing_fields=["specific_center"],
            )
        if self.place_resolver is None or not callable(getattr(self.place_resolver, "search_nearby", None)):
            return self._record_result(
                "error", "附近地点查询暂时不可用，请稍后重试。"
            )
        try:
            places = await self.place_resolver.search_nearby(
                keywords=decision.nearby_category,
                location=f"{center.longitude:.6f},{center.latitude:.6f}",
                radius_m=2000,
                limit=6,
            )
        except (AmapMcpAPIError, AmapMcpConfigurationError) as exc:
            logger.warning("普通模式附近地点查询失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error" if isinstance(exc, AmapMcpAPIError) else "error",
                "附近地点查询暂时不可用，请稍后重试。",
                retryable=isinstance(exc, AmapMcpAPIError),
            )
        except Exception as exc:  # noqa: BLE001 - ordinary chat must fail closed
            logger.warning("普通模式附近地点查询失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error",
                "附近地点查询暂时不可用，请稍后重试。",
                retryable=True,
            )
        if not places:
            message = (
                f"暂时没有在{center.name}附近找到{decision.nearby_category}。"
                f"可以告诉我另一个参考地点，我继续帮你找。"
            )
            return self._record_result(
                "ok",
                message,
                data={"center": center.name, "category": decision.nearby_category, "places": []},
            )
        self.last_card = {
            "type": "nearby",
            "nearby": {
                "center_name": center.name,
                "center_longitude": center.longitude,
                "center_latitude": center.latitude,
                "category": decision.nearby_category,
                "radius_m": 2000,
                "places": places,
            },
        }
        summaries = []
        for index, place in enumerate(places[:4], start=1):
            distance = place.get("distance_m")
            distance_text = _format_distance(distance) if isinstance(distance, int) else "距离未知"
            address = f"（{place['address']}）" if place.get("address") else ""
            summaries.append(f"{index}. {place['name']}，距中心直线约{distance_text}{address}")
        message = f"{center.name}附近找到这些{decision.nearby_category}：\n" + "\n".join(summaries)
        return self._record_result(
            "ok",
            message,
            data={
                "center": center.name,
                "category": decision.nearby_category,
                "places": [
                    {
                        "name": place.get("name"),
                        "address": place.get("address"),
                        "distance_m": place.get("distance_m"),
                    }
                    for place in places[:6]
                ],
            },
        )

    async def query_route(self, decision: GeneralIntentDecision) -> str:
        self.last_card = None
        self.last_tool_result = None
        if "origin" in decision.missing_fields:
            return self._record_result(
                "needs_clarification",
                "要查询这条路线，我还需要出发地点。请告诉我你从哪里出发。",
                missing_fields=["origin"],
            )
        if "destination" in decision.missing_fields:
            return self._record_result(
                "needs_clarification",
                "要查询这条路线，我还需要目的地。请告诉我你想去哪里。",
                missing_fields=["destination"],
            )
        if not decision.origin or not decision.destination:
            return self._record_result(
                "needs_clarification",
                "请同时告诉我路线的出发地点和目的地。",
                missing_fields=["origin", "destination"],
            )
        try:
            origin = await self._resolve_place(decision.origin)
            destination = await self._resolve_place(decision.destination)
        except Exception as exc:  # noqa: BLE001 - return safe status to the Agent
            logger.warning("普通模式路线地点解析失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error", "路线地点解析暂时失败，请稍后重试。", retryable=True
            )
        if origin is None or destination is None:
            missing = decision.origin if origin is None else decision.destination
            return self._record_result(
                "needs_clarification",
                f"暂时无法确认“{missing}”的位置，请补充所在城市或更具体的地点名称。",
                missing_fields=["specific_place"],
            )
        if self.route_service is None:
            return self._record_result(
                "error", "路线服务暂时不可用，我不能给出未经查询的路线耗时。请稍后重试。"
            )
        try:
            segment = await self.route_service.resolve_segment(
                origin,
                destination,
                requested_mode=decision.route_mode,
                city_adcode="",
            )
        except (RouteAPIError, RouteConfigurationError) as exc:
            logger.warning("普通模式路线服务失败：%s", type(exc).__name__)
            retryable = isinstance(exc, RouteAPIError)
            return self._record_result(
                "retryable_error" if retryable else "error",
                "路线服务暂时不可用，我不能给出未经查询的路线耗时。请稍后重试。",
                retryable=retryable,
            )
        except Exception as exc:  # noqa: BLE001 - ordinary chat must fail closed
            logger.warning("普通模式路线查询失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error",
                "路线查询暂时失败，请稍后重试。",
                retryable=True,
            )
        if segment.duration_min is None or segment.distance_m is None:
            return self._record_result(
                "retryable_error",
                f"已识别{origin.name}和{destination.name}，但当前没有获得可用路线，请稍后重试。",
                retryable=True,
            )
        self.last_card = {
            "type": "route",
            "route": segment.model_dump(mode="json"),
        }
        distance = _format_distance(segment.distance_m)
        mode = {"walking": "步行", "driving": "驾车", "transit": "公共交通"}[segment.mode]
        message = (
            f"{origin.name}到{destination.name}：建议{mode}，距离约{distance}，"
            f"预计{segment.duration_min}分钟。"
        )
        estimated = bool(getattr(segment, "estimated", False))
        if estimated:
            message += "当前为估算结果，并非实时导航路线。"
        return self._record_result(
            "partial" if estimated else "ok",
            message,
            data={
                "origin": origin.name,
                "destination": destination.name,
                "mode": segment.mode,
                "distance_m": segment.distance_m,
                "duration_min": segment.duration_min,
                "estimated": estimated,
            },
        )

    async def query_weather(self, decision: GeneralIntentDecision) -> str:
        self.last_card = None
        self.last_tool_result = None
        if "city" in decision.missing_fields or not decision.city:
            return self._record_result(
                "needs_clarification",
                "查询天气还需要查询天气的城市，请告诉我城市名称。",
                missing_fields=["city"],
            )
        city = self.catalog_repository.find_city(decision.city)
        if city is None:
            return self._record_result(
                "needs_clarification",
                f"暂时无法识别城市“{decision.city}”，请换用完整城市名称。",
                missing_fields=["recognized_city"],
            )
        today = self.today_provider()
        target_dates = sorted(
            set(decision.forecast_dates or [decision.target_date or today])
        )
        if any(target_date < today or target_date > today + timedelta(days=9) for target_date in target_dates):
            return self._record_result(
                "error", "天气服务目前只能查询今天起 10 天内的预报，请换一个日期。"
            )
        reference = self.attraction_repository.first_located_for_city(city.id)
        if reference is None:
            return self._record_result(
                "error", f"{city.name}目前缺少可用于天气查询的坐标，暂时无法获取预报。"
            )
        attraction, _ = reference
        if self.weather_client is None:
            return self._record_result("error", "天气服务暂时不可用，请稍后重试。")
        try:
            get_weather_range = getattr(self.weather_client, "get_weather_range", None)
            if callable(get_weather_range):
                weather_results = await get_weather_range(
                    city=city.name,
                    target_dates=target_dates,
                    longitude=attraction.longitude,
                    latitude=attraction.latitude,
                )
            else:
                weather_results = await asyncio.gather(
                    *(
                        self.weather_client.get_weather(
                            city=city.name,
                            target_date=target_date,
                            longitude=attraction.longitude,
                            latitude=attraction.latitude,
                        )
                        for target_date in target_dates
                    )
                )
        except QWeatherAPIError:
            return self._record_result(
                "retryable_error", "天气服务暂时不可用，请稍后重试。", retryable=True
            )
        except QWeatherConfigurationError:
            return self._record_result("error", "天气服务暂时不可用，请稍后重试。")
        except Exception as exc:  # noqa: BLE001 - ordinary chat must fail closed
            logger.warning("普通模式天气查询失败：%s", type(exc).__name__)
            return self._record_result(
                "retryable_error", "天气服务暂时不可用，请稍后重试。", retryable=True
            )
        results_by_date = {
            weather.date: weather
            for weather in weather_results
            if weather.date is not None
        }
        if (
            len(weather_results) != len(target_dates)
            or any(weather.status != "available" for weather in weather_results)
            or any(target_date not in results_by_date for target_date in target_dates)
        ):
            missing_dates = [
                item.isoformat()
                for item in target_dates
                if item not in results_by_date or results_by_date[item].status != "available"
            ]
            return self._record_result(
                "partial",
                f"天气服务暂时没有返回完整的{len(target_dates)}天预报，请稍后重试。",
                data={"city": city.name, "available_dates": sorted(results_by_date)},
                retryable=True,
                missing_fields=missing_dates,
            )
        weather_results = [results_by_date[target_date] for target_date in target_dates]
        weather_payloads = [weather.model_dump(mode="json") for weather in weather_results]
        self.last_card = {
            "type": "weather",
            "weather": weather_payloads[0] if len(weather_payloads) == 1 else weather_payloads,
        }
        summaries = []
        for target_date, weather in zip(target_dates, weather_results):
            facts = [weather.condition or "天气状况未知"]
            if weather.temperature_min is not None and weather.temperature_max is not None:
                facts.append(f"{weather.temperature_min:g}～{weather.temperature_max:g}℃")
            if weather.precipitation_probability is not None:
                probability = weather.precipitation_probability
                percentage = round(probability * 100 if probability <= 1 else probability)
                facts.append(f"降水概率约{percentage}%")
            if weather.wind_level:
                facts.append(f"风力{weather.wind_level}级")
            summaries.append(f"{city.name}{format_date_zh(weather.date or target_date)}：{'，'.join(facts)}。")
        if len(summaries) == 1:
            return self._record_result(
                "ok",
                summaries[0],
                data={
                    "city": city.name,
                    "forecasts": [
                        {
                            "date": weather.date.isoformat() if weather.date else target_dates[0].isoformat(),
                            "condition": weather.condition,
                            "temperature_min": weather.temperature_min,
                            "temperature_max": weather.temperature_max,
                            "precipitation_probability": weather.precipitation_probability,
                            "wind_level": weather.wind_level,
                        }
                        for weather in weather_results
                    ],
                },
            )
        starts_today = target_dates[0] == today
        expected_dates = [
            (today if starts_today else today + timedelta(days=1))
            + timedelta(days=offset)
            for offset in range(len(target_dates))
        ]
        if target_dates == expected_dates and starts_today:
            heading = f"{city.name}从今天起{len(summaries)}天的天气："
        elif target_dates == expected_dates:
            heading = f"{city.name}未来{len(summaries)}天的天气："
        else:
            heading = f"{city.name}以下日期的天气："
        message = heading + "\n" + "\n".join(summaries)
        return self._record_result(
            "ok",
            message,
            data={
                "city": city.name,
                "forecasts": [
                    {
                        "date": weather.date.isoformat() if weather.date else target_date.isoformat(),
                        "condition": weather.condition,
                        "temperature_min": weather.temperature_min,
                        "temperature_max": weather.temperature_max,
                        "precipitation_probability": weather.precipitation_probability,
                        "wind_level": weather.wind_level,
                    }
                    for target_date, weather in zip(target_dates, weather_results)
                ],
            },
        )

    async def _resolve_place(self, name: str) -> AttractionStop | None:
        local = self.attraction_repository.find_place(name)
        if local is not None:
            attraction, city = local
            if attraction.longitude is not None and attraction.latitude is not None:
                return AttractionStop(
                    poi_id=attraction.poi_id,
                    name=attraction.name,
                    city=city.name,
                    category=attraction.category,
                    description=attraction.description,
                    longitude=attraction.longitude,
                    latitude=attraction.latitude,
                    visit_duration_minutes=attraction.visit_duration_minutes,
                )
        if self.place_resolver is not None:
            return await self.place_resolver.resolve(name)
        return None


def build_general_travel_tools(session, settings) -> GeneralTravelToolService:
    from backend.attractions import AttractionRepository
    from backend.destination.repository import CatalogRepository

    cache = build_cache(settings)
    place_resolver = None
    route_client = None
    try:
        mcp_client = AmapMcpClient(
            settings.amap_api_key,
            endpoint=settings.amap_mcp_url,
            timeout_seconds=settings.amap_mcp_timeout_seconds,
            sse_read_timeout_seconds=settings.amap_mcp_sse_read_timeout_seconds,
        )
        place_resolver = AmapMcpPlaceResolver(
            mcp_client,
            detail_concurrency=getattr(settings, "amap_mcp_detail_concurrency", 5),
        )
        route_client = AmapMcpRouteClient(
            settings.amap_api_key,
            endpoint=settings.amap_mcp_url,
            timeout_seconds=settings.amap_mcp_timeout_seconds,
            sse_read_timeout_seconds=settings.amap_mcp_sse_read_timeout_seconds,
            cache=cache,
            cache_ttl_seconds=settings.redis_route_ttl_seconds,
            mcp_client=mcp_client,
        )
    except (AmapMcpConfigurationError, RouteConfigurationError):
        pass
    weather_client = QWeatherClient(
        settings.qweather_api_key,
        api_host=settings.qweather_api_host,
        timeout_seconds=settings.qweather_timeout_seconds,
        cache=cache,
        cache_ttl_seconds=settings.redis_weather_ttl_seconds,
    )
    return GeneralTravelToolService(
        catalog_repository=CatalogRepository(session),
        attraction_repository=AttractionRepository(session),
        route_service=RouteEnrichmentService(
            route_client,
            max_concurrency=settings.route_enrichment_concurrency,
        ),
        place_resolver=place_resolver,
        weather_client=weather_client,
    )


def _coordinates(value: object) -> tuple[float, float] | None:
    if not isinstance(value, str) or "," not in value:
        return None
    longitude, latitude = value.split(",", maxsplit=1)
    try:
        return float(longitude), float(latitude)
    except ValueError:
        return None


def _valid_coordinates(
    coordinates: tuple[float, float] | None,
) -> tuple[float, float] | None:
    if coordinates is None:
        return None
    longitude, latitude = coordinates
    if (
        not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
        or (longitude == 0 and latitude == 0)
    ):
        return None
    return longitude, latitude


def _detail_record(payload: Mapping[str, object]) -> Mapping[str, object]:
    for key in ("poi", "detail", "result"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            return nested
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], Mapping):
        return results[0]
    nested_data = payload.get("data")
    if isinstance(nested_data, Mapping):
        for key in ("poi", "detail", "result"):
            nested = nested_data.get(key)
            if isinstance(nested, Mapping):
                return nested
        if "location" in nested_data:
            return nested_data
    return payload


def _number_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _straight_line_distance_m(
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> float:
    origin_longitude, origin_latitude = map(math.radians, origin)
    destination_longitude, destination_latitude = map(math.radians, destination)
    longitude_delta = destination_longitude - origin_longitude
    latitude_delta = destination_latitude - origin_latitude
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude)
        * math.cos(destination_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, haversine)))


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            result = _text(item)
            if result:
                return result
    return None


def _format_distance(distance_m: int) -> str:
    if distance_m < 1000:
        return f"{distance_m}米"
    return f"{distance_m / 1000:.1f}公里"


__all__ = [
    "AmapMcpPlaceResolver",
    "GeneralTravelToolService",
    "build_general_travel_tools",
]
