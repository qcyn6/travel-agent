from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, timedelta

from backend.amap_mcp import AmapMcpAPIError, AmapMcpClient, AmapMcpConfigurationError
from backend.cache import build_cache
from backend.models.general_chat import GeneralIntentDecision
from backend.models.travel_plan import AttractionStop
from backend.requirements.dates import format_date_zh
from backend.route import AmapMcpRouteClient, RouteConfigurationError, RouteEnrichmentService
from backend.weather import QWeatherAPIError, QWeatherClient, QWeatherConfigurationError


logger = logging.getLogger(__name__)


class AmapMcpPlaceResolver:
    """Resolve a free-form place name to coordinates through Amap MCP."""

    def __init__(self, client: AmapMcpClient):
        self.client = client

    async def resolve(self, name: str, *, city: str | None = None) -> AttractionStop | None:
        arguments = {"address": name}
        if city:
            arguments["city"] = city
        try:
            payload = await self.client.call_tool("maps_geo", arguments, operation="geocode")
        except (AmapMcpAPIError, AmapMcpConfigurationError):
            return None
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            return None
        item = geocodes[0]
        if not isinstance(item, Mapping):
            return None
        coordinates = _coordinates(item.get("location"))
        if coordinates is None:
            return None
        longitude, latitude = coordinates
        resolved_city = _text(item.get("city")) or city or _text(item.get("province")) or "未知城市"
        return AttractionStop(
            poi_id=f"general:{name}",
            name=_text(item.get("formatted_address")) or name,
            city=resolved_city,
            longitude=longitude,
            latitude=latitude,
        )


class GeneralTravelToolService:
    """Execute bounded route and weather queries for ordinary chat."""

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

    async def execute(self, decision: GeneralIntentDecision) -> str | None:
        if decision.intent == "route":
            return await self.query_route(decision)
        if decision.intent == "weather":
            return await self.query_weather(decision)
        return None

    async def query_route(self, decision: GeneralIntentDecision) -> str:
        if "origin" in decision.missing_fields:
            return "要查询这条路线，我还需要出发地点。请告诉我你从哪里出发。"
        if "destination" in decision.missing_fields:
            return "要查询这条路线，我还需要目的地。请告诉我你想去哪里。"
        if not decision.origin or not decision.destination:
            return "请同时告诉我路线的出发地点和目的地。"
        origin = await self._resolve_place(decision.origin)
        destination = await self._resolve_place(decision.destination)
        if origin is None or destination is None:
            missing = decision.origin if origin is None else decision.destination
            return f"暂时无法确认“{missing}”的位置，请补充所在城市或更具体的地点名称。"
        if self.route_service is None:
            return "路线服务暂时不可用，我不能给出未经查询的路线耗时。请稍后重试。"
        segment = await self.route_service.resolve_segment(
            origin,
            destination,
            requested_mode=decision.route_mode,
            city_adcode="",
        )
        if segment.duration_min is None or segment.distance_m is None:
            return f"已识别{origin.name}和{destination.name}，但当前没有获得可用路线，请稍后重试。"
        distance = _format_distance(segment.distance_m)
        mode = {"walking": "步行", "driving": "驾车", "transit": "公共交通"}[segment.mode]
        if segment.estimated or segment.source == "haversine":
            source = "本地距离估算，并非实时路线"
        else:
            source = "高德路线查询"
        return (
            f"{origin.name}到{destination.name}：建议{mode}，距离约{distance}，"
            f"预计{segment.duration_min}分钟。数据来源：{source}。"
        )

    async def query_weather(self, decision: GeneralIntentDecision) -> str:
        if "city" in decision.missing_fields or not decision.city:
            return "查询天气还需要查询天气的城市，请告诉我城市名称。"
        city = self.catalog_repository.find_city(decision.city)
        if city is None:
            return f"暂时无法识别城市“{decision.city}”，请换用完整城市名称。"
        target_date = decision.target_date or self.today_provider()
        today = self.today_provider()
        if target_date < today or target_date > today + timedelta(days=9):
            return "天气服务目前只能查询今天起 10 天内的预报，请换一个日期。"
        reference = self.attraction_repository.first_located_for_city(city.id)
        if reference is None:
            return f"{city.name}目前缺少可用于天气查询的坐标，暂时无法获取预报。"
        attraction, _ = reference
        if self.weather_client is None:
            return "天气服务暂时不可用，请稍后重试。"
        try:
            weather = await self.weather_client.get_weather(
                city=city.name,
                target_date=target_date,
                longitude=attraction.longitude,
                latitude=attraction.latitude,
            )
        except (QWeatherAPIError, QWeatherConfigurationError):
            return "天气服务暂时不可用，请稍后重试。"
        except Exception as exc:  # noqa: BLE001 - ordinary chat must fail closed
            logger.warning("普通模式天气查询失败：%s", type(exc).__name__)
            return "天气服务暂时不可用，请稍后重试。"
        if weather.status != "available":
            return f"暂时没有获得{city.name}{format_date_zh(target_date)}的天气预报。"
        facts = [weather.condition or "天气状况未知"]
        if weather.temperature_min is not None and weather.temperature_max is not None:
            facts.append(f"{weather.temperature_min:g}～{weather.temperature_max:g}℃")
        if weather.precipitation_probability is not None:
            probability = weather.precipitation_probability
            percentage = round(probability * 100 if probability <= 1 else probability)
            facts.append(f"降水概率约{percentage}%")
        if weather.wind_level:
            facts.append(f"风力{weather.wind_level}级")
        return f"{city.name}{format_date_zh(target_date)}：{'，'.join(facts)}。数据来源：和风天气。"

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
        place_resolver = AmapMcpPlaceResolver(mcp_client)
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
