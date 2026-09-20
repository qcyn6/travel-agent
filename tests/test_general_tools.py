from datetime import date
from types import SimpleNamespace

import pytest

from backend.conversation.general_tools import GeneralTravelToolService
from backend.models.general_chat import GeneralIntentDecision
from backend.models.route import RouteSegment
from backend.models.weather import WeatherResult


class AttractionRepositoryStub:
    def __init__(self):
        self.places = {
            "杭州东站": (
                SimpleNamespace(
                    poi_id="origin",
                    name="杭州东站",
                    category="交通设施",
                    description="",
                    longitude=120.21,
                    latitude=30.29,
                    visit_duration_minutes=90,
                ),
                SimpleNamespace(name="杭州市"),
            ),
            "西湖": (
                SimpleNamespace(
                    poi_id="destination",
                    name="西湖",
                    category="自然风光",
                    description="",
                    longitude=120.14,
                    latitude=30.25,
                    visit_duration_minutes=120,
                ),
                SimpleNamespace(name="杭州市"),
            ),
        }

    def find_place(self, name):
        return self.places.get(name)

    def first_located_for_city(self, city_id):
        assert city_id == 1
        return self.places["西湖"]


class RouteServiceStub:
    async def resolve_segment(self, origin, destination, *, requested_mode, city_adcode):
        assert requested_mode == "transit"
        assert city_adcode == ""
        return RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            distance_m=8300,
            duration_min=32,
            mode="transit",
            source="amap_mcp",
        )


class WeatherClientStub:
    async def get_weather(self, *, city, target_date, longitude, latitude):
        assert (longitude, latitude) == (120.14, 30.25)
        return WeatherResult(
            city=city,
            date=target_date,
            condition="多云",
            temperature_min=20,
            temperature_max=28,
            precipitation_probability=0.2,
            wind_level="2",
            status="available",
            source="qweather",
        )


def _service(**overrides):
    values = {
        "catalog_repository": SimpleNamespace(
            find_city=lambda name: SimpleNamespace(id=1, name="杭州市") if name in {"杭州", "杭州市"} else None
        ),
        "attraction_repository": AttractionRepositoryStub(),
        "route_service": RouteServiceStub(),
        "weather_client": WeatherClientStub(),
        "today_provider": lambda: date(2026, 9, 20),
    }
    values.update(overrides)
    return GeneralTravelToolService(**values)


@pytest.mark.asyncio
async def test_route_tool_returns_source_distance_and_duration():
    reply = await _service().execute(
        GeneralIntentDecision(
            intent="route",
            origin="杭州东站",
            destination="西湖",
        )
    )

    assert "公共交通" in reply
    assert "8.3公里" in reply
    assert "32分钟" in reply
    assert "高德路线查询" in reply


@pytest.mark.asyncio
async def test_weather_tool_returns_structured_facts():
    reply = await _service().execute(
        GeneralIntentDecision(
            intent="weather",
            city="杭州市",
            target_date=date(2026, 9, 21),
        )
    )

    assert "多云" in reply
    assert "20～28℃" in reply
    assert "降水概率约20%" in reply
    assert "和风天气" in reply


@pytest.mark.asyncio
async def test_weather_tool_rejects_date_outside_forecast_window():
    reply = await _service().execute(
        GeneralIntentDecision(
            intent="weather",
            city="杭州市",
            target_date=date(2026, 10, 1),
        )
    )

    assert "10 天内" in reply
