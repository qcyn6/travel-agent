import httpx
import pytest

from backend.models.travel_plan import AttractionStop, TravelDay
from backend.route import AmapRouteClient, RouteAPIError, RouteEnrichmentService


def stop(
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
    )


@pytest.mark.asyncio
async def test_amap_route_maps_distance_and_duration():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/direction/walking"
        assert request.url.params["show_fields"] == "cost"
        assert request.url.params["origin_id"] == "A1"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {"distance": "1850", "cost": {"duration": "1230"}}
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapRouteClient(
            "test-key",
            base_url="https://amap.test",
            client=client,
        ).get_route(
            stop("A1", "西湖", 120.148, 30.242),
            stop("A2", "灵隐寺", 120.102, 30.241),
            mode="walking",
            city_adcode="330100",
        )

    assert result.distance_m == 1850
    assert result.duration_min == 21
    assert result.source == "amap"
    assert result.estimated is False


@pytest.mark.asyncio
async def test_amap_route_retries_once_for_server_error():
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {"distance": "1000", "cost": {"duration": "600"}}
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapRouteClient(
            "test-key",
            base_url="https://amap.test",
            max_retries=2,
            client=client,
        ).get_route(
            stop("A1", "起点", 120.1, 30.2),
            stop("A2", "终点", 120.2, 30.3),
            mode="driving",
            city_adcode="330100",
        )

    assert calls == 2
    assert result.duration_min == 10


@pytest.mark.asyncio
async def test_route_enrichment_falls_back_to_estimate():
    class FailedClient:
        async def get_route(self, *args, **kwargs):
            raise RouteAPIError("offline")

    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[
            stop("A1", "西湖", 120.148, 30.242),
            stop("A2", "灵隐寺", 120.102, 30.241),
        ],
        description="用于验证路线服务降级的测试行程。",
    )

    result = await RouteEnrichmentService(FailedClient()).enrich_day(
        day,
        requested_mode="auto",
    )

    assert len(result.routes) == 1
    assert result.routes[0].source == "haversine"
    assert result.routes[0].estimated is True
    assert result.routes[0].distance_m > 0
    assert result.routes[0].duration_min > 0


@pytest.mark.asyncio
async def test_route_enrichment_marks_missing_coordinates_unavailable():
    class UnusedClient:
        async def get_route(self, *args, **kwargs):
            raise AssertionError("缺少坐标时不应调用高德")

    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[
            stop("A1", "起点", None, None),
            stop("A2", "终点", 120.2, 30.3),
        ],
        description="用于验证缺少坐标时的测试行程。",
    )

    result = await RouteEnrichmentService(UnusedClient()).enrich_day(
        day,
        requested_mode="auto",
    )

    assert result.routes[0].source == "unavailable"
    assert result.routes[0].distance_m is None
