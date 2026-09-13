from datetime import date

from backend.evaluator import PlanEvaluator
from backend.models.attraction_facts import OpeningHours, OpeningPeriod
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


def test_dense_schedule_requires_replanning():
    result = PlanEvaluator().evaluate(_plan(_day(total_minutes=690)), _requirement())

    assert result.passed is False
    assert result.replan_required is True
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


def test_rain_with_mostly_outdoor_stops_is_reported():
    stops = [
        AttractionStop(poi_id="A1", name="西湖", city="杭州市", environment="outdoor"),
        AttractionStop(poi_id="A2", name="公园", city="杭州市", environment="outdoor"),
    ]
    weather = WeatherResult(
        city="杭州市", date=date(2026, 9, 14), condition="大雨",
        precipitation_probability=0.8, status="available", source="qweather",
    )

    result = PlanEvaluator().evaluate(
        _plan(_day(stops=stops, weather=weather)), _requirement()
    )

    assert any(issue.type == "weather" and issue.severity == "high" for issue in result.issues)


def test_known_last_entry_conflict_is_hard_but_unknown_is_ignored():
    known = OpeningHours(
        status="known",
        periods=[OpeningPeriod(weekdays=[1], open_time="09:00", close_time="17:00", last_entry_time="16:30")],
        confidence="high",
    )
    stops = [
        AttractionStop(
            poi_id="A1", name="博物馆", city="杭州市", start_time="16:55",
            end_time="18:00", opening_hours=known,
        ),
        AttractionStop(
            poi_id="A2", name="公园", city="杭州市", start_time="18:10",
            end_time="19:00", opening_hours=OpeningHours(status="unknown"),
        ),
    ]

    result = PlanEvaluator().evaluate(_plan(_day(stops=stops)), _requirement())

    opening_issues = [issue for issue in result.issues if issue.type == "opening_hours"]
    assert len(opening_issues) == 1
    assert opening_issues[0].severity == "high"


def test_preference_coverage_is_a_soft_issue():
    result = PlanEvaluator().evaluate(
        _plan(_day()), _requirement(["自然", "美食", "历史文化"])
    )

    issue = next(issue for issue in result.issues if issue.type == "preference")
    assert issue.severity == "medium"
    assert result.replan_required is False
