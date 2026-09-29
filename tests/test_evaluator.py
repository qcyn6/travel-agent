from datetime import date

from backend.evaluator import PlanEvaluator
from backend.models.hotel import HotelStay
from backend.models.requirement import TravelRequirement
from backend.models.route import RouteSegment
from backend.models.schedule import DaySchedule
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan
from backend.models.weather import WeatherResult


def _plan(day: TravelDay) -> TravelPlan:
    return TravelPlan(
        province="浙江省",
        city_stays=[{"city": "杭州市", "days": 1}],
        city_plans=[{"city": "杭州市", "days": [{"day": 1, "attractions": ["西湖"], "description": "这是用于评估器测试的行程说明。"}]}],
        itinerary=[day],
    )


def _requirement(interests=None):
    return TravelRequirement(
        cities=["杭州市"], days=1, interests=interests or [], travel_scope="single_city"
    )


def _day(*, stops=None, routes=None, total_minutes=300, weather=None):
    return TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=date(2026, 9, 14),
        attractions=stops or [AttractionStop(poi_id="A1", name="西湖", city="杭州市")],
        routes=routes or [],
        weather=weather,
        schedule=DaySchedule(
            start_time="09:00", end_time="14:00", visit_minutes=150, route_minutes=0,
            meal_minutes=90, buffer_minutes=60, total_minutes=total_minutes, status="complete",
        ),
        description="测试行程",
    )


def test_daily_schedule_density_is_not_part_of_evaluation():
    result = PlanEvaluator().evaluate(_plan(_day(total_minutes=690)), _requirement())

    assert result.passed is True
    assert all(issue.type != "schedule_density" for issue in result.issues)
    assert result.daily_metrics[0].attraction_count == 1
    assert result.daily_metrics[0].route_minutes == 0


def test_long_route_is_reported_by_rules():
    first = AttractionStop(poi_id="A1", name="甲", city="杭州市")
    second = AttractionStop(poi_id="A2", name="乙", city="杭州市")
    route = RouteSegment(
        origin_poi_id="A1", origin_name="甲", destination_poi_id="A2", destination_name="乙",
        duration_min=80, mode="transit", source="amap",
    )

    result = PlanEvaluator().evaluate(
        _plan(_day(stops=[first, second], routes=[route])), _requirement()
    )

    assert any(issue.type == "route" and issue.severity == "medium" for issue in result.issues)


def test_daily_route_total_remains_a_high_severity_route_check():
    stops = [
        AttractionStop(poi_id="A1", name="甲", city="杭州市"),
        AttractionStop(poi_id="A2", name="乙", city="杭州市"),
    ]
    routes = [
        RouteSegment(
            origin_poi_id=f"A{index}",
            origin_name="甲",
            destination_poi_id=f"A{index + 1}",
            destination_name="乙",
            duration_min=70,
            mode="transit",
            source="amap_mcp",
        )
        for index in range(1, 4)
    ]

    result = PlanEvaluator().evaluate(
        _plan(_day(stops=stops, routes=routes)), _requirement()
    )

    assert result.passed is False
    assert result.daily_metrics[0].route_minutes == 210
    assert any(
        issue.type == "route" and issue.severity == "high"
        and "交通总时长" in issue.message
        for issue in result.issues
    )


def test_evaluator_counts_previous_hotel_transfer_in_daily_route_metrics():
    stop = AttractionStop(poi_id="A1", name="首站", city="杭州市")
    hotel = HotelStay(
        poi_id="H0", name="前一晚酒店", city="杭州市", longitude=120.0, latitude=30.0
    )
    route = RouteSegment(
        origin_poi_id=hotel.poi_id,
        origin_name=hotel.name,
        destination_poi_id=stop.poi_id,
        destination_name=stop.name,
        duration_min=35,
        mode="driving",
        source="amap_mcp",
    )
    day = _day(stops=[stop]).model_copy(
        update={"start_hotel": hotel, "start_hotel_route": route}
    )

    result = PlanEvaluator().evaluate(_plan(day), _requirement())

    assert result.daily_metrics[0].route_minutes == 35
    assert result.daily_metrics[0].route_segments == 1


def test_estimated_route_ratio_counts_all_five_map_segments():
    stops = [
        AttractionStop(poi_id=f"A{index}", name=f"景点{index}", city="杭州市")
        for index in range(1, 5)
    ]
    start_hotel = HotelStay(poi_id="H0", name="前一晚酒店", city="杭州市")
    end_hotel = HotelStay(poi_id="H1", name="当晚酒店", city="杭州市")
    start_route = RouteSegment(
        origin_poi_id="H0", origin_name="前一晚酒店", destination_poi_id="A1",
        destination_name="景点1", duration_min=42, mode="driving",
        source="haversine", estimated=True,
    )
    attraction_routes = [
        RouteSegment(
            origin_poi_id=f"A{index}", origin_name=f"景点{index}",
            destination_poi_id=f"A{index + 1}", destination_name=f"景点{index + 1}",
            duration_min=minutes, mode="transit", source="amap_mcp",
        )
        for index, minutes in enumerate((7, 37, 108), start=1)
    ]
    end_route = RouteSegment(
        origin_poi_id="A4", origin_name="景点4", destination_poi_id="H1",
        destination_name="当晚酒店", duration_min=25, mode="driving", source="amap_mcp",
    )
    day = _day(stops=stops, routes=attraction_routes).model_copy(update={
        "start_hotel": start_hotel,
        "start_hotel_route": start_route,
        "hotel": end_hotel,
        "hotel_route": end_route,
    })

    metric = PlanEvaluator().evaluate(_plan(day), _requirement()).daily_metrics[0]

    assert metric.route_minutes == 194
    assert metric.route_segments == 5
    assert metric.estimated_route_ratio == 1 / 5


def test_estimated_end_hotel_route_counts_in_ratio_not_schedule_minutes():
    stops = [
        AttractionStop(poi_id=f"A{index}", name=f"景点{index}", city="杭州市")
        for index in range(1, 4)
    ]
    start_hotel = HotelStay(poi_id="H0", name="前一晚酒店", city="杭州市")
    end_hotel = HotelStay(poi_id="H1", name="当晚酒店", city="杭州市")
    start_route = RouteSegment(
        origin_poi_id="H0", origin_name="前一晚酒店", destination_poi_id="A1",
        destination_name="景点1", duration_min=12, mode="driving", source="amap_mcp",
    )
    attraction_routes = [
        RouteSegment(
            origin_poi_id=f"A{index}", origin_name=f"景点{index}",
            destination_poi_id=f"A{index + 1}", destination_name=f"景点{index + 1}",
            duration_min=minutes, mode="transit", source="amap_mcp",
        )
        for index, minutes in enumerate((20, 30), start=1)
    ]
    end_route = RouteSegment(
        origin_poi_id="A3", origin_name="景点3", destination_poi_id="H1",
        destination_name="当晚酒店", duration_min=70, mode="driving",
        source="haversine", estimated=True,
    )
    day = _day(stops=stops, routes=attraction_routes).model_copy(update={
        "start_hotel": start_hotel,
        "start_hotel_route": start_route,
        "hotel": end_hotel,
        "hotel_route": end_route,
    })

    metric = PlanEvaluator().evaluate(_plan(day), _requirement()).daily_metrics[0]

    assert metric.route_minutes == 62
    assert metric.route_segments == 4
    assert metric.estimated_route_ratio == 1 / 4


def test_weather_does_not_add_an_attraction_conflict_without_environment_data():
    stops = [
        AttractionStop(poi_id="A1", name="西湖", city="杭州市"),
        AttractionStop(poi_id="A2", name="公园", city="杭州市"),
    ]
    weather = WeatherResult(
        city="杭州市", date=date(2026, 9, 14), condition="大雨",
        precipitation_probability=0.8, status="available", source="qweather",
    )

    result = PlanEvaluator().evaluate(
        _plan(_day(stops=stops, weather=weather)), _requirement()
    )

    assert all(issue.type != "weather" for issue in result.issues)
    assert result.passed is True


def test_weather_does_not_hide_another_hard_issue():
    stops = [
        AttractionStop(poi_id="A1", name="西湖", city="杭州市"),
        AttractionStop(poi_id="A2", name="公园", city="杭州市"),
    ]
    weather = WeatherResult(
        city="杭州市", date=date(2026, 9, 14), condition="大雨",
        precipitation_probability=0.8, status="available", source="qweather",
    )

    unavailable = RouteSegment(
        origin_poi_id="A1", origin_name="西湖", destination_poi_id="A2",
        destination_name="公园", duration_min=None, mode="transit", source="unavailable",
    )
    result = PlanEvaluator().evaluate(
        _plan(_day(stops=stops, routes=[unavailable], total_minutes=690, weather=weather)),
        _requirement(),
    )

    assert all(issue.type != "weather" for issue in result.issues)
    assert any(issue.type == "route" and issue.severity == "high" for issue in result.issues)


def test_evaluator_warns_when_known_opening_hours_conflict_with_visit():
    stops = [
        AttractionStop(
            poi_id="A1", name="博物馆", city="杭州市", start_time="16:55",
            end_time="18:00", visit_duration_minutes=65,
            opening_hours_weekly="周一至周日 09:00-17:00",
        ),
        AttractionStop(
            poi_id="A2", name="公园", city="杭州市", start_time="18:10",
            end_time="19:00",
        ),
    ]

    result = PlanEvaluator().evaluate(_plan(_day(stops=stops)), _requirement())

    issue = next(issue for issue in result.issues if issue.type == "schedule")
    assert "博物馆" in issue.message
    assert "营业时间" in issue.message


def test_evaluator_warns_when_schedule_exceeds_user_time_window():
    day = _day().model_copy(update={
        "schedule": DaySchedule(
            start_time="09:00", end_time="18:00", visit_minutes=150,
            route_minutes=0, meal_minutes=90, buffer_minutes=60,
            total_minutes=300, status="complete",
        ),
        "attractions": [AttractionStop(
            poi_id="A1", name="西湖", city="杭州市", start_time="09:00", end_time="18:00",
        )],
    })
    requirement = _requirement().model_copy(update={
        "daily_start_time": "09:00", "daily_end_time": "17:00",
    })

    result = PlanEvaluator().evaluate(_plan(day), requirement)

    assert any(
        issue.type == "schedule" and "超过你设置的17:00" in issue.message
        for issue in result.issues
    )


def test_evaluator_includes_manual_replacement_warnings():
    plan = _plan(_day()).model_copy(update={
        "manual_adjustment_warnings": {1: ["更换后的景点可能超出当天时间窗口。"]},
    })

    result = PlanEvaluator().evaluate(plan, _requirement())

    assert any(
        issue.type == "manual_adjustment"
        and issue.message == "更换后的景点可能超出当天时间窗口。"
        for issue in result.issues
    )


def test_interest_coverage_is_not_part_of_evaluation_or_daily_metrics():
    result = PlanEvaluator().evaluate(
        _plan(_day()), _requirement(["自然", "美食", "历史文化"])
    )

    assert all(issue.type != "preference" for issue in result.issues)
    assert result.daily_metrics[0].preference_coverage is None


def test_daily_metrics_report_estimated_route_ratio():
    first = AttractionStop(poi_id="A1", name="甲", city="杭州市")
    second = AttractionStop(poi_id="A2", name="乙", city="杭州市")
    third = AttractionStop(poi_id="A3", name="丙", city="杭州市")
    routes = [
        RouteSegment(
            origin_poi_id="A1", origin_name="甲", destination_poi_id="A2", destination_name="乙",
            duration_min=20, mode="walking", source="haversine", estimated=True,
        ),
        RouteSegment(
            origin_poi_id="A2", origin_name="乙", destination_poi_id="A3", destination_name="丙",
            duration_min=10, mode="walking", source="amap_mcp",
        ),
    ]
    result = PlanEvaluator().evaluate(
        _plan(_day(stops=[first, second, third], routes=routes)),
        _requirement(),
    )

    metric = result.daily_metrics[0]
    assert metric.route_minutes == 30
    assert metric.route_segments == 2
    assert metric.estimated_route_ratio == 0.5
    assert any(
        issue.type == "route" and "不是地图真实路线" in issue.message
        for issue in result.issues
    )


def test_evaluator_reports_unavailable_weather_as_external_degradation():
    weather = WeatherResult(
        city="杭州市",
        date=date(2026, 9, 14),
        status="unavailable",
        source="none",
    )

    result = PlanEvaluator().evaluate(
        _plan(_day(weather=weather)),
        _requirement(),
    )

    issue = next(issue for issue in result.issues if issue.type == "weather")
    assert issue.severity == "low"
    assert result.passed is True


def test_evaluator_deduplicates_identical_issues():
    first = AttractionStop(poi_id="A1", name="甲", city="杭州市")
    second = AttractionStop(poi_id="A2", name="乙", city="杭州市")
    route = RouteSegment(
        origin_poi_id="A1", origin_name="甲", destination_poi_id="A2", destination_name="乙",
        duration_min=80, mode="transit", source="amap",
    )
    day = _day(stops=[first, second], routes=[route], total_minutes=690)
    plan = _plan(day.model_copy(update={"day": 1}))
    result = PlanEvaluator().evaluate(plan, _requirement())

    keys = [(issue.type, issue.day, issue.message) for issue in result.issues]
    assert len(keys) == len(set(keys))
