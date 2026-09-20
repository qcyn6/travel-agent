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

    assert result.schedule.total_minutes == 400
    assert result.schedule.end_time == "14:40"
    assert result.schedule.status == "complete"
    assert [(stop.start_time, stop.end_time) for stop in result.attractions] == [
        ("08:00", "10:00"),
        ("10:50", "12:20"),
        ("13:40", "14:40"),
    ]


def test_schedule_does_not_add_buffer_after_final_attraction():
    first = AttractionStop(poi_id="A1", name="古镇", city="杭州市", visit_duration_minutes=120)
    second = AttractionStop(poi_id="A2", name="公园", city="杭州市", visit_duration_minutes=90)
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[first, second],
        routes=[_route(first, second, 30)],
        description="最后一个景点不加缓冲",
    )

    result = ScheduleBuilder().build_day(day)

    assert result.schedule.buffer_minutes == 20
    assert result.schedule.end_time == "13:00"
    assert result.attractions[-1].end_time == "12:20"


def test_schedule_drops_attractions_that_would_cross_21_or_midnight():
    stops = [
        AttractionStop(poi_id=f"A{index}", name=f"景点{index}", city="昆明市", visit_duration_minutes=240)
        for index in range(1, 4)
    ]
    day = TravelDay(
        day=1,
        city="昆明市",
        city_adcode="530100",
        city_day=1,
        attractions=stops,
        routes=[_route(stops[0], stops[1], 30), _route(stops[1], stops[2], 30)],
        description="边界测试",
    )

    result = ScheduleBuilder().build_day(day)

    assert [stop.name for stop in result.attractions] == ["景点1", "景点2"]
    assert len(result.routes) == 1
    assert result.schedule.start_time == "08:00"
    assert result.schedule.end_time <= "21:00"
    assert all(
        stop.start_time >= "08:00" and stop.end_time <= "21:00"
        for stop in result.attractions
    )


def test_schedule_uses_user_selected_same_day_window():
    first = AttractionStop(poi_id="A1", name="古镇", city="杭州市", visit_duration_minutes=120)
    second = AttractionStop(poi_id="A2", name="博物馆", city="杭州市", visit_duration_minutes=120)
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[first, second],
        routes=[_route(first, second, 30)],
        description="用户时间窗口",
    )

    result = ScheduleBuilder().build_day(
        day,
        day_start_time="10:00",
        day_end_time="18:00",
    )

    assert result.schedule.start_time == "10:00"
    assert result.schedule.end_time <= "18:00"
    assert result.attractions[0].start_time == "10:00"
    assert all(stop.end_time <= "18:00" for stop in result.attractions)


def test_schedule_exact_rejects_a_route_that_would_exceed_the_window():
    first = AttractionStop(poi_id="A1", name="起点", city="杭州市", visit_duration_minutes=120)
    second = AttractionStop(poi_id="A2", name="终点", city="杭州市", visit_duration_minutes=120)
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[first, second],
        routes=[_route(first, second, 30)],
        description="严格时间窗口测试",
    )

    assert ScheduleBuilder(meal_minutes=0, buffer_minutes=0).try_build_day_exact(
        day,
        day_start_time="10:00",
        day_end_time="14:00",
    ) is None


def test_schedule_exact_rejects_missing_route_duration():
    first = AttractionStop(poi_id="A1", name="起点", city="杭州市")
    second = AttractionStop(poi_id="A2", name="终点", city="杭州市")
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[first, second],
        routes=[_route(first, second, None)],
        description="严格路线完整性测试",
    )

    assert ScheduleBuilder().try_build_day_exact(day) is None


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


def test_schedule_uses_database_duration_without_duration_source():
    first = AttractionStop(
        poi_id="A1", name="古镇", city="丽江市", estimated_visit_minutes=150,
        duration_source="local_knowledge",
    )
    second = AttractionStop(
        poi_id="A2", name="公园", city="丽江市", estimated_visit_minutes=90,
        duration_source="web_extracted",
    )
    day = TravelDay(
        day=1, city="丽江市", city_adcode="530700", city_day=1,
        attractions=[first, second], routes=[_route(first, second, 20)],
        description="测试行程",
    )

    result = ScheduleBuilder().build_day(day)

    assert result.schedule.status == "complete"


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
