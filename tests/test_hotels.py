from datetime import date

import pytest

from backend.hotels import AmapHotelSearchService, HotelEnrichmentService
from backend.models.hotel import HotelStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan
from backend.schedule import ScheduleBuilder


def _day() -> TravelDay:
    return TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[
            AttractionStop(
                poi_id="A1",
                name="西湖",
                city="杭州市",
                longitude=120.148,
                latitude=30.242,
            )
        ],
        description="酒店收尾测试行程",
    )


@pytest.mark.asyncio
async def test_hotel_enrichment_selects_nearest_route_and_keeps_it_out_of_routes():
    hotels = [
        HotelStay(
            poi_id="H1",
            name="较远酒店",
            city="杭州市",
            longitude=120.20,
            latitude=30.30,
            image_url="https://example.com/far.jpg",
        ),
        HotelStay(
            poi_id="H2",
            name="最近酒店",
            city="杭州市",
            longitude=120.16,
            latitude=30.25,
            image_url="https://example.com/near.jpg",
        ),
    ]

    class Searcher:
        async def search(self, **_kwargs):
            return hotels

    class Routes:
        async def resolve_segment(self, origin, destination, **_kwargs):
            distance = 1800 if destination.poi_id == "H1" else 700
            return RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                distance_m=distance,
                duration_min=20 if distance == 1800 else 9,
                mode="transit",
                source="amap_mcp",
            )

    day = _day()
    plan = TravelPlan(
        city_stays=[{"city": "杭州市", "days": 1}],
        city_plans=[
            CityPlan(
                city="杭州市",
                days=[DayPlan(day=1, attractions=["西湖"], description="Hotel overnight test schedule")],
            )
        ],
        itinerary=[day],
    )
    enriched = await HotelEnrichmentService(Searcher(), Routes()).enrich_plan(plan)
    result = enriched.itinerary[0]

    assert result.hotel is not None
    assert result.hotel.poi_id == "H2"
    assert result.hotel.image_url == "https://example.com/near.jpg"
    assert result.hotel_route is not None
    assert result.hotel_route.destination_poi_id == "H2"
    assert result.routes == []

    scheduled = ScheduleBuilder().build_day(result)
    assert scheduled.schedule.route_minutes == 0
    assert scheduled.hotel_route == result.hotel_route


@pytest.mark.asyncio
async def test_amap_hotel_search_keeps_coordinates_rating_and_photo():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "1",
                "pois": [
                    {
                        "id": "H1",
                        "name": "湖畔酒店",
                        "cityname": "杭州市",
                        "address": "西湖路 1 号",
                        "location": "120.150000,30.243000",
                        "business": {"rating": "4.6"},
                        "photos": [{"url": "https://example.com/hotel.jpg"}],
                    }
                ],
            }

    class Client:
        async def get(self, _url, *, params):
            assert params["keywords"] == "酒店"
            assert params["sortrule"] == "distance"
            return Response()

    hotels = await AmapHotelSearchService(
        "test-key",
        client=Client(),
    ).search(city="杭州市", origin=_day().attractions[0])

    assert hotels[0].poi_id == "H1"
    assert hotels[0].longitude == 120.15
    assert hotels[0].latitude == 30.243
    assert hotels[0].rating == 4.6
    assert hotels[0].image_url == "https://example.com/hotel.jpg"


@pytest.mark.asyncio
async def test_overnight_connection_skips_first_stop_if_hotel_transfer_arrives_too_early():
    previous_hotel = HotelStay(
        poi_id="H0",
        name="前一晚酒店",
        city="杭州市",
        longitude=120.10,
        latitude=30.20,
    )
    first = AttractionStop(
        poi_id="A1",
        name="九点开放景点",
        city="杭州市",
        longitude=120.11,
        latitude=30.21,
        visit_duration_minutes=60,
        opening_hours_weekly="周一至周日:09:00-17:00",
    )
    second = AttractionStop(
        poi_id="A2",
        name="八点开放景点",
        city="杭州市",
        longitude=120.12,
        latitude=30.22,
        visit_duration_minutes=60,
        opening_hours_weekly="周一至周日:08:00-17:00",
    )

    class Routes:
        def __init__(self):
            self.calls = []

        async def resolve_segment(self, origin, destination, **_kwargs):
            self.calls.append((origin.poi_id, destination.poi_id))
            return RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                distance_m=1000,
                duration_min=45 if destination.poi_id == "A1" else 30,
                mode="walking",
                source="amap_mcp",
            )

    routes = Routes()
    service = HotelEnrichmentService(searcher=None, route_service=routes)
    day = TravelDay(
        day=2,
        city="杭州市",
        city_adcode="330100",
        city_day=2,
        date=date(2026, 9, 22),
        attractions=[first, second],
        routes=[
            RouteSegment(
                origin_poi_id="A1",
                origin_name=first.name,
                destination_poi_id="A2",
                destination_name=second.name,
                distance_m=1000,
                duration_min=10,
                mode="walking",
                source="amap_mcp",
            )
        ],
        description="酒店转场测试",
    )

    connected = await service.connect_overnight(
        previous_hotel,
        day,
        daily_start_time="08:00",
        daily_end_time="18:00",
    )

    assert [stop.poi_id for stop in connected.attractions] == ["A2"]
    assert connected.start_hotel == previous_hotel
    assert connected.start_hotel_route.destination_poi_id == "A2"
    assert connected.start_hotel_route.duration_min == 30
    assert connected.routes == []
    assert routes.calls == [("H0", "A1"), ("H0", "A2")]


@pytest.mark.asyncio
async def test_amap_hotel_search_retries_empty_results_and_ignores_empty_cache():
    calls = 0
    cached_values = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            if calls == 1:
                return {"status": "1", "pois": []}
            return {
                "status": "1",
                "pois": [
                    {
                        "id": "H1",
                        "name": "重试后酒店",
                        "cityname": "杭州市",
                        "location": "120.150000,30.243000",
                    }
                ],
            }

    class Client:
        async def get(self, _url, *, params):
            nonlocal calls
            calls += 1
            return Response()

    class Cache:
        async def get_json(self, _key):
            return []

        async def set_json(self, _key, value, *, ttl_seconds):
            cached_values.append((value, ttl_seconds))
            return True

    hotels = await AmapHotelSearchService(
        "test-key",
        max_retries=2,
        cache=Cache(),
        client=Client(),
    ).search(city="杭州市", origin=_day().attractions[0])

    assert calls == 2
    assert [hotel.name for hotel in hotels] == ["重试后酒店"]
    assert cached_values and cached_values[0][0][0]["name"] == "重试后酒店"
