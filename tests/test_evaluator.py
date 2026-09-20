from datetime import date

from backend.evaluator import PlanEvaluator
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


def test_dense_schedule_is_reported_for_user_confirmation():
    result = PlanEvaluator().evaluate(_plan(_day(total_minutes=690)), _requirement())

    assert result.passed is False
    assert result.issues[0].type == "schedule_density"
    assert result.issues[0].severity == "high"


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

    result = PlanEvaluator().evaluate(
        _plan(_day(stops=stops, total_minutes=690, weather=weather)), _requirement()
    )

    assert all(issue.type != "weather" for issue in result.issues)
    assert any(issue.type == "schedule_density" for issue in result.issues)


def test_evaluator_does_not_check_opening_hours():
    stops = [
        AttractionStop(
            poi_id="A1", name="博物馆", city="杭州市", start_time="16:55",
            end_time="18:00",
        ),
        AttractionStop(
            poi_id="A2", name="公园", city="杭州市", start_time="18:10",
            end_time="19:00",
        ),
    ]

    result = PlanEvaluator().evaluate(_plan(_day(stops=stops)), _requirement())

    assert all(issue.type != "opening_hours" for issue in result.issues)


def test_preference_coverage_is_a_soft_issue():
    result = PlanEvaluator().evaluate(
        _plan(_day()), _requirement(["自然", "美食", "历史文化"])
    )

    issue = next(issue for issue in result.issues if issue.type == "preference")
    assert issue.severity == "medium"


def test_preference_coverage_prefers_transient_catalogue_tags():
    plan = _plan(_day(stops=[
        AttractionStop(poi_id="A1", name="无关名称", city="杭州市"),
    ]))

    result = PlanEvaluator().evaluate_with_context(
        plan,
        _requirement(["自然", "历史文化", "美食"]),
        interest_tags_by_poi={"A1": ["自然"]},
    )

    issue = next(issue for issue in result.issues if issue.type == "preference")
    assert "33%" in issue.message


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
