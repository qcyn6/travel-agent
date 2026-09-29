import pytest

from backend.models.travel_plan import AttractionStop, TravelDay
from backend.route import (
    AmapMcpRouteClient,
    RouteAPIError,
    RouteEnrichmentService,
)


def stop(
    poi_id: str,
    name: str,
    longitude: float = 120.148,
    latitude: float = 30.242,
    city: str = "杭州市",
) -> AttractionStop:
    return AttractionStop(
        poi_id=poi_id,
        name=name,
        city=city,
        longitude=longitude,
        latitude=latitude,
    )


class FakeMcp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call_tool(self, name, arguments, *, operation):
        self.calls.append((name, arguments, operation))
        response = self.responses[name]
        return response() if callable(response) else response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "tool", "payload"),
    [
        (
            "walking",
            "maps_direction_walking",
            {"route": {"paths": [{
                "distance": "6161",
                "duration": "4929",
                "steps": [
                    {"polyline": "120.148,30.242;120.130,30.241"},
                    {"polyline": "120.130,30.241;120.102,30.241"},
                ],
            }]}},
        ),
        ("driving", "maps_direction_driving", {"paths": [{"distance": "7852", "duration": "1786"}]}),
        ("transit", "maps_direction_transit_integrated", {"distance": "4530", "transits": [{"duration": "3974"}]}),
    ],
)
async def test_amap_mcp_route_client_maps_all_transport_modes(mode, tool, payload):
    mcp = FakeMcp({tool: payload})
    client = AmapMcpRouteClient("test-key", mcp_client=mcp)

    result = await client.get_route(
        stop("A1", "西湖"),
        stop("A2", "灵隐寺", longitude=120.102, latitude=30.241),
        mode=mode,
        city_adcode="330100",
    )

    assert result.source == "amap_mcp"
    assert result.distance_m > 0
    assert result.duration_min > 0
    assert mcp.calls[0][0] == tool
    if mode == "transit":
        assert mcp.calls[0][1]["city"] == "杭州市"
        assert mcp.calls[0][1]["cityd"] == "杭州市"
    if mode == "walking":
        assert result.polyline == [
            (120.148, 30.242),
            (120.13, 30.241),
            (120.102, 30.241),
        ]


@pytest.mark.asyncio
async def test_local_distance_is_used_for_auto_mode_without_mcp_distance_call():
    class Client:
        def __init__(self):
            self.distance_calls = 0
            self.route_modes = []

        async def get_distance(self, *_args, **_kwargs):
            self.distance_calls += 1
            raise AssertionError("自动交通方式不应调用高德距离接口")

        async def get_route(self, _origin, _destination, *, mode, **_kwargs):
            self.route_modes.append(mode)
            from backend.models.route import RouteSegment

            return RouteSegment(
                origin_poi_id="A1",
                origin_name="西湖",
                destination_poi_id="A2",
                destination_name="灵隐寺",
                origin_longitude=120.148,
                origin_latitude=30.242,
                destination_longitude=120.102,
                destination_latitude=30.241,
                distance_m=1200,
                duration_min=16,
                mode=mode,
                source="amap_mcp",
            )

    client = Client()
    result = await RouteEnrichmentService(client).enrich_day(
        TravelDay(
            day=1,
            city="杭州市",
            city_adcode="330100",
            city_day=1,
            attractions=[stop("A1", "西湖"), stop("A2", "灵隐寺", 120.102, 30.241)],
            description="测试行程",
        ),
        requested_mode="auto",
    )

    assert client.distance_calls == 0
    assert client.route_modes == ["transit"]
    assert result.routes[0].source == "amap_mcp"


@pytest.mark.asyncio
async def test_amap_mcp_distance_client_uses_straight_line_tool():
    mcp = FakeMcp(
        {
            "maps_distance": {
                "results": [{"origin_id": "1", "dest_id": "1", "distance": "4425"}]
            }
        }
    )
    client = AmapMcpRouteClient("test-key", mcp_client=mcp)

    assert await client.get_distance(stop("A1", "西湖"), stop("A2", "灵隐寺")) == 4425
    assert mcp.calls == [
        (
            "maps_distance",
            {"origins": "120.148000,30.242000", "destination": "120.148000,30.242000", "type": "0"},
            "distance",
        )
    ]


@pytest.mark.asyncio
async def test_auto_mode_uses_local_distance_when_route_fails():
    class Client:
        async def get_route(self, _origin, _destination, *, mode, **_kwargs):
            raise RouteAPIError("route offline")

    result = await RouteEnrichmentService(Client()).enrich_day(
        TravelDay(
            day=1,
            city="杭州市",
            city_adcode="330100",
            city_day=1,
            attractions=[stop("A1", "西湖"), stop("A2", "灵隐寺", 120.102, 30.241)],
            description="测试行程",
        ),
        requested_mode="auto",
    )

    assert result.routes[0].mode == "transit"
    assert result.routes[0].source == "haversine"
    assert result.routes[0].estimated is True
    assert result.routes[0].distance_m > 0
    assert result.routes[0].duration_min > 0
