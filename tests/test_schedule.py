from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.schedule import ScheduleBuilder


def _route(origin: AttractionStop, destination: AttractionStop, duration: int | None, *, estimated=False):
    return RouteSegment(
        origin_poi_id=origin.poi_id,
        origin_name=origin.name,
        destination_poi_id=destination.poi_id,
        destination_name=destination.name,
        duration_min=duration,
        mode="walking",
        source="haversine" if estimated else ("amap" if duration is not None else "unavailable"),
        estimated=estimated,
    )


def test_schedule_combines_visits_routes_meal_and_buffer():
    stops = [
        AttractionStop(
            poi_id="A1", name="博物馆", city="杭州市", estimated_visit_minutes=120,
            duration_source="web_extracted",
        ),
        AttractionStop(
            poi_id="A2", name="古塔", city="杭州市", estimated_visit_minutes=90,
            duration_source="web_extracted",
        ),
        AttractionStop(
            poi_id="A3", name="公园", city="杭州市", estimated_visit_minutes=60,
            duration_source="web_extracted",
        ),
    ]
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=stops,
        routes=[_route(stops[0], stops[1], 30), _route(stops[1], stops[2], 20)],
        description="测试行程",
    )

    result = ScheduleBuilder().build_day(day)

    assert result.schedule.total_minutes == 470
    assert result.schedule.end_time == "16:50"
    assert result.schedule.status == "complete"
    assert [(stop.start_time, stop.end_time) for stop in result.attractions] == [
        ("09:00", "11:00"),
        ("11:50", "13:20"),
        ("15:30", "16:30"),
    ]


def test_schedule_marks_category_and_haversine_data_as_estimated():
    first = AttractionStop(
        poi_id="A1", name="公园", city="杭州市", estimated_visit_minutes=120,
        duration_source="category_fallback",
    )
    second = AttractionStop(
        poi_id="A2", name="古塔", city="杭州市", estimated_visit_minutes=90,
        duration_source="web_extracted",
    )
    day = TravelDay(
        day=1, city="杭州市", city_adcode="330100", city_day=1,
        attractions=[first, second], routes=[_route(first, second, 20, estimated=True)],
        description="测试行程",
    )

    result = ScheduleBuilder().build_day(day)

    assert result.schedule.status == "estimated"


def test_schedule_marks_missing_route_as_incomplete():
    first = AttractionStop(poi_id="A1", name="甲", city="杭州市", estimated_visit_minutes=90)
    second = AttractionStop(poi_id="A2", name="乙", city="杭州市", estimated_visit_minutes=90)
    day = TravelDay(
        day=1, city="杭州市", city_adcode="330100", city_day=1,
        attractions=[first, second], routes=[_route(first, second, None)], description="测试行程",
    )

    result = ScheduleBuilder().build_day(day)

    assert result.schedule.status == "incomplete"
    assert result.schedule.route_minutes == 0
