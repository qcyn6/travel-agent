import asyncio
from datetime import date, timedelta

import httpx
import pytest

from backend.models.travel_plan import AttractionStop, TravelDay
from backend.models.weather import WeatherResult
from backend.weather import QWeatherClient, WeatherEnrichmentService


def day(day_date: date | None) -> TravelDay:
    return TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=day_date,
        attractions=[
            AttractionStop(
                poi_id="A1",
                name="西湖",
                city="杭州市",
                longitude=120.148,
                latitude=30.242,
            )
        ],
        description="用于验证天气补全服务的测试行程。",
    )


@pytest.mark.asyncio
async def test_qweather_client_maps_daily_forecast():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-QW-Api-Key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "days": [
                    {
                        "forecastStartTime": "2026-09-15T00:00+08:00",
                        "temperatureMin": {"value": 20},
                        "temperatureMax": {"value": 29},
                        "daytime": {
                            "condition": {"text": "小雨"},
                            "precipitation": {"probability": 0.6},
                            "wind": {"scale": 3},
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await QWeatherClient(
            "test-key",
            api_host="weather.test",
            client=client,
        ).get_weather(
            city="杭州市",
            target_date=date(2026, 9, 15),
            longitude=120.148,
            latitude=30.242,
        )

    assert result.status == "available"
    assert result.condition == "小雨"
    assert result.temperature_min == 20
    assert result.precipitation_probability == 0.6


@pytest.mark.asyncio
async def test_weather_service_skips_without_date():
    service = WeatherEnrichmentService(None, today_provider=lambda: date(2026, 9, 13))
    result = await service.enrich_day(day(None))

    assert result.weather.status == "skipped"


@pytest.mark.asyncio
async def test_weather_service_marks_far_date_unavailable_without_call():
    class UnusedClient:
        async def get_weather(self, **kwargs):
            raise AssertionError("远期日期不应调用天气 API")

    service = WeatherEnrichmentService(UnusedClient(), today_provider=lambda: date(2026, 9, 13))
    result = await service.enrich_day(day(date(2026, 10, 3)))

    assert result.weather.status == "unavailable"


@pytest.mark.asyncio
async def test_weather_enrichment_runs_days_with_a_bound():
    class Client:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def get_weather(self, *, city, target_date, **_kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.02)
                return WeatherResult(
                    city=city,
                    date=target_date,
                    condition="晴",
                    temperature_min=20,
                    temperature_max=30,
                    status="available",
                    source="qweather",
                )
            finally:
                self.active -= 1

    client = Client()
    today = date(2026, 9, 17)
    days = [day(today + timedelta(days=index)) for index in range(4)]
    service = WeatherEnrichmentService(
        client,
        max_concurrency=2,
        today_provider=lambda: today,
    )

    results = await asyncio.gather(*(service.enrich_day(item) for item in days))

    assert client.max_active == 2
    assert [item.weather.source for item in results] == ["qweather"] * 4
