"""Controlled route/weather integration acceptance.

The default command runs fixture clients only.  ``--mode live`` is an explicit
opt-in that constructs the configured Amap MCP and QWeather clients and makes
real requests.  Both modes exercise the same enrichment services and report
the same fallback semantics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from backend.config.settings import Settings, get_settings
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.models.weather import WeatherResult
from backend.route import AmapMcpRouteClient, RouteAPIError, RouteConfigurationError
from backend.route import RouteEnrichmentService
from backend.weather import QWeatherAPIError, QWeatherClient, WeatherEnrichmentService


@dataclass(frozen=True)
class ToolAcceptanceResult:
    case_id: str
    tool: str
    status: str
    source: str
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "tool": self.tool,
            "status": self.status,
            "source": self.source,
            "details": self.details,
        }


async def run_fixture_acceptance() -> list[ToolAcceptanceResult]:
    """Run repeatable route/weather checks without a database or network."""

    today = date(2026, 9, 20)
    origin = _stop("fixture-origin", "西湖", 120.148, 30.242)
    destination = _stop("fixture-destination", "灵隐寺", 120.102, 30.241)
    route_service = RouteEnrichmentService(_FixtureRouteClient())
    route = await route_service.resolve_segment(
        origin,
        destination,
        requested_mode="walking",
        city_adcode="330100",
    )

    estimated_route = await RouteEnrichmentService(_FailingRouteClient()).resolve_segment(
        origin,
        destination,
        requested_mode="auto",
        city_adcode="330100",
    )
    missing_route = await route_service.resolve_segment(
        _stop("fixture-missing", "缺少坐标景点", None, None),
        destination,
        requested_mode="auto",
        city_adcode="330100",
    )

    weather_day = _day(origin, target_date=today + timedelta(days=1))
    weather = await WeatherEnrichmentService(
        _FixtureWeatherClient(),
        today_provider=lambda: today,
    ).enrich_day(weather_day)
    unavailable_weather = await WeatherEnrichmentService(
        _FailingWeatherClient(),
        today_provider=lambda: today,
    ).enrich_day(weather_day)

    return [
        ToolAcceptanceResult(
            case_id="route-success",
            tool="route",
            status="pass" if route.source == "amap_mcp" and route.duration_min else "fail",
            source=route.source,
            details={
                "duration_min": route.duration_min,
                "distance_m": route.distance_m,
                "estimated": route.estimated,
            },
        ),
        ToolAcceptanceResult(
            case_id="route-estimated-fallback",
            tool="route",
            status=(
                "pass"
                if estimated_route.source == "haversine" and estimated_route.estimated
                else "fail"
            ),
            source=estimated_route.source,
            details={
                "duration_min": estimated_route.duration_min,
                "distance_m": estimated_route.distance_m,
                "estimated": estimated_route.estimated,
            },
        ),
        ToolAcceptanceResult(
            case_id="route-missing-coordinates",
            tool="route",
            status="pass" if missing_route.source == "unavailable" else "fail",
            source=missing_route.source,
            details={
                "duration_min": missing_route.duration_min,
                "distance_m": missing_route.distance_m,
                "estimated": missing_route.estimated,
            },
        ),
        ToolAcceptanceResult(
            case_id="weather-success",
            tool="weather",
            status="pass" if weather.weather and weather.weather.status == "available" else "fail",
            source=weather.weather.source if weather.weather else "none",
            details={
                "status": weather.weather.status if weather.weather else "missing",
                "condition": weather.weather.condition if weather.weather else None,
                "date": str(weather.weather.date) if weather.weather else None,
            },
        ),
        ToolAcceptanceResult(
            case_id="weather-unavailable-fallback",
            tool="weather",
            status=(
                "pass"
                if unavailable_weather.weather
                and unavailable_weather.weather.status == "unavailable"
                else "fail"
            ),
            source=unavailable_weather.weather.source if unavailable_weather.weather else "none",
            details={
                "status": (
                    unavailable_weather.weather.status
                    if unavailable_weather.weather
                    else "missing"
                ),
                "date": (
                    str(unavailable_weather.weather.date)
                    if unavailable_weather.weather
                    else None
                ),
            },
        ),
    ]


async def run_live_acceptance(settings: Settings | None = None) -> list[ToolAcceptanceResult]:
    """Run the same checks against configured external services.

    The caller must opt in by choosing the CLI's ``--mode live``.  Missing
    configuration is reported as an error instead of silently switching to a
    fixture, so a live acceptance report cannot be mistaken for a real call.
    """

    configured = settings or get_settings()
    if not configured.amap_api_key:
        raise RuntimeError("live 验收需要配置 AMAP_API_KEY")
    if not configured.qweather_api_key or not configured.qweather_api_host:
        raise RuntimeError("live 验收需要配置 QWEATHER_API_KEY 和 QWEATHER_API_HOST")

    try:
        route_client = AmapMcpRouteClient(
            configured.amap_api_key,
            endpoint=configured.amap_mcp_url,
            timeout_seconds=configured.amap_mcp_timeout_seconds,
            sse_read_timeout_seconds=configured.amap_mcp_sse_read_timeout_seconds,
        )
    except (RouteConfigurationError, RouteAPIError) as exc:
        raise RuntimeError(f"高德 MCP 配置失败：{exc}") from exc
    route_service = RouteEnrichmentService(route_client)
    weather_service = WeatherEnrichmentService(
        QWeatherClient(
            configured.qweather_api_key,
            api_host=configured.qweather_api_host,
            timeout_seconds=configured.qweather_timeout_seconds,
        )
    )

    origin = _stop("live-origin", "西湖", 120.148, 30.242)
    destination = _stop("live-destination", "灵隐寺", 120.102, 30.241)
    route = await route_service.resolve_segment(
        origin,
        destination,
        requested_mode="walking",
        city_adcode="330100",
    )
    day = _day(origin, target_date=date.today() + timedelta(days=1))
    weather_day = await weather_service.enrich_day(day)
    return [
        ToolAcceptanceResult(
            case_id="route-live",
            tool="route",
            status="pass" if route.source == "amap_mcp" and route.duration_min else "degraded",
            source=route.source,
            details={
                "duration_min": route.duration_min,
                "distance_m": route.distance_m,
                "estimated": route.estimated,
            },
        ),
        ToolAcceptanceResult(
            case_id="weather-live",
            tool="weather",
            status=(
                "pass"
                if weather_day.weather and weather_day.weather.status == "available"
                else "degraded"
            ),
            source=weather_day.weather.source if weather_day.weather else "none",
            details={
                "status": weather_day.weather.status if weather_day.weather else "missing",
                "condition": weather_day.weather.condition if weather_day.weather else None,
                "date": str(weather_day.weather.date) if weather_day.weather else None,
            },
        ),
    ]


def write_acceptance_report(
    results: list[ToolAcceptanceResult], output_path: str | Path
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [result.to_dict() for result in results]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _stop(
    poi_id: str,
    name: str,
    longitude: float | None,
    latitude: float | None,
) -> AttractionStop:
    return AttractionStop(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        longitude=longitude,
        latitude=latitude,
        visit_duration_minutes=90,
    )


def _day(stop: AttractionStop, *, target_date: date) -> TravelDay:
    return TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=target_date,
        attractions=[stop],
        description="工具联调验收固定行程。",
    )


class _FixtureRouteClient:
    async def get_route(self, origin, destination, *, mode, **_kwargs):
        return RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            origin_longitude=origin.longitude,
            origin_latitude=origin.latitude,
            destination_longitude=destination.longitude,
            destination_latitude=destination.latitude,
            distance_m=7_200,
            duration_min=24,
            mode=mode,
            source="amap_mcp",
        )


class _FailingRouteClient:
    async def get_route(self, *_args, **_kwargs):
        raise RouteAPIError("fixture route service unavailable")


class _FixtureWeatherClient:
    async def get_weather(self, *, city, target_date, **_kwargs):
        return WeatherResult(
            city=city,
            date=target_date,
            condition="晴",
            temperature_min=20,
            temperature_max=30,
            precipitation_probability=0.1,
            wind_level="2",
            status="available",
            source="qweather",
        )


class _FailingWeatherClient:
    async def get_weather(self, **_kwargs):
        raise QWeatherAPIError("fixture weather service unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description="路线与天气工具联调验收")
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="fixture",
        help="fixture 不访问外部服务；live 显式调用已配置的高德和和风服务",
    )
    parser.add_argument(
        "--output",
        default="experiments/outputs/tool_acceptance.json",
        help="验收报告 JSON 输出路径",
    )
    args = parser.parse_args()
    if args.mode == "live":
        results = asyncio.run(run_live_acceptance())
    else:
        results = asyncio.run(run_fixture_acceptance())
    path = write_acceptance_report(results, args.output)
    print(f"完成 {len(results)} 条 {args.mode} 工具验收，报告：{path}")
    for result in results:
        print(f"{result.case_id}: {result.status} ({result.source})")


if __name__ == "__main__":
    main()


__all__ = [
    "ToolAcceptanceResult",
    "run_fixture_acceptance",
    "run_live_acceptance",
    "write_acceptance_report",
]
