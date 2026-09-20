import asyncio

import pytest

from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.route import RouteAPIError, RouteEnrichmentService


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
async def test_route_enrichment_exposes_single_segment_resolution():
    class Client:
        async def get_route(self, origin, destination, *, mode, **kwargs):
            return RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                mode=mode,
                source="amap_mcp",
                distance_m=900,
                duration_min=12,
            )

    result = await RouteEnrichmentService(Client()).resolve_segment(
        stop("A1", "起点", 120.1, 30.2),
        stop("A2", "终点", 120.11, 30.21),
        requested_mode="walking",
        city_adcode="330100",
    )

    assert result.source == "amap_mcp"
    assert result.duration_min == 12


@pytest.mark.asyncio
async def test_route_enrichment_uses_distance_estimate_when_mcp_route_fails():
    class FailedClient:
        async def get_route(self, *args, **kwargs):
            raise RouteAPIError("route offline")

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
        requested_mode="walking",
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


@pytest.mark.asyncio
async def test_route_enrichment_uses_distance_estimate_without_client():
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[
            stop("A1", "西湖", 120.148, 30.242),
            stop("A2", "灵隐寺", 120.102, 30.241),
        ],
        description="用于验证没有路线客户端时的距离估算。",
    )

    result = await RouteEnrichmentService(None).enrich_day(
        day,
        requested_mode="auto",
    )

    assert result.routes[0].mode == "transit"
    assert result.routes[0].source == "haversine"
    assert result.routes[0].estimated is True
    assert result.routes[0].duration_min > 0


@pytest.mark.asyncio
async def test_route_enrichment_runs_independent_segments_with_a_bound():
    class Client:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def get_route(self, origin, destination, *, mode, **_kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.02)
                return RouteSegment(
                    origin_poi_id=origin.poi_id,
                    origin_name=origin.name,
                    destination_poi_id=destination.poi_id,
                    destination_name=destination.name,
                    mode=mode,
                    source="amap_mcp",
                    distance_m=1000,
                    duration_min=15,
                )
            finally:
                self.active -= 1

    client = Client()
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[
            stop("A1", "西湖", 120.148, 30.242),
            stop("A2", "灵隐寺", 120.102, 30.241),
            stop("A3", "六和塔", 120.100, 30.205),
            stop("A4", "龙井村", 120.113, 30.228),
        ],
        description="用于验证路线段并发的测试行程。",
    )

    result = await RouteEnrichmentService(client, max_concurrency=2).enrich_day(
        day,
        requested_mode="walking",
    )

    assert client.max_active == 2
    assert [(route.origin_poi_id, route.destination_poi_id) for route in result.routes] == [
        ("A1", "A2"),
        ("A2", "A3"),
        ("A3", "A4"),
    ]
