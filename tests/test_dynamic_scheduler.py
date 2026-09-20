import pytest

from backend.models.city_stay import CityStay
from backend.models.planning import PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.models.route import RouteSegment
from backend.planner.dynamic_scheduler import (
    DynamicCityScheduler,
    calculate_distance_score,
    calculate_priority,
    haversine_distance_m,
)
from backend.schedule import ScheduleBuilder


def candidate(
    poi_id: str,
    name: str,
    *,
    preference_score: float,
    longitude: float | None = 120.0,
    latitude: float | None = 30.0,
    duration: int = 90,
) -> PlanningCandidate:
    return PlanningCandidate(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        category="景区",
        interest_tags=["自然"],
        description=f"{name}的景点描述。",
        longitude=longitude,
        latitude=latitude,
        visit_duration_minutes=duration,
        preference_score=preference_score,
        keyword_score=preference_score,
        semantic_score=preference_score,
        tag_match_count=1,
    )


def requirement(days: int = 1, *, start: str | None = None, end: str | None = None):
    return TravelRequirement(
        province="浙江省",
        cities=["杭州市"],
        days=days,
        interests=["自然"],
        travel_scope="single_city",
        daily_start_time=start,
        daily_end_time=end,
    )


def test_haversine_and_distance_score_handle_zero_and_missing_coordinates():
    assert haversine_distance_m(120.0, 30.0, 120.0, 30.0) == pytest.approx(0)
    assert haversine_distance_m(120.0, 30.0, None, 30.0) is None
    assert calculate_distance_score(0, 0) == pytest.approx(1.0)
    assert calculate_distance_score(50, 100) == pytest.approx(0.5)
    assert calculate_distance_score(None, 100) == pytest.approx(0.0)


def test_priority_uses_report_defined_weights():
    assert calculate_priority(0.8, 0.5) == pytest.approx(0.71)


def test_first_stop_uses_preference_and_later_stop_recalculates_distance_priority():
    scheduler = DynamicCityScheduler(meal_minutes=0, buffer_minutes=0)
    result = scheduler.plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(),
        candidates=[
            candidate("A", "首站高偏好", preference_score=0.9, longitude=120.0),
            candidate("B", "远处高偏好", preference_score=0.82, longitude=120.3),
            candidate("C", "近处次高偏好", preference_score=0.8, longitude=120.01),
        ],
    )

    assert result.city_plan.days[0].attractions[:2] == ["首站高偏好", "近处次高偏好"]
    selected = [decision.name for decision in result.decisions if decision.selected]
    assert selected[:2] == ["首站高偏好", "近处次高偏好"]
    second = next(
        decision
        for decision in result.decisions
        if decision.name == "近处次高偏好"
    )
    assert second.distance_m is not None
    assert second.distance_score > 0.9


def test_missing_coordinates_do_not_create_fake_distance():
    result = DynamicCityScheduler(meal_minutes=0, buffer_minutes=0).plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(),
        candidates=[
            candidate("A", "有坐标首站", preference_score=0.9),
            candidate("B", "缺坐标景点", preference_score=0.8, longitude=None, latitude=None),
        ],
    )

    missing = next(decision for decision in result.decisions if decision.poi_id == "B")
    assert missing.distance_m is None
    assert missing.distance_score == 0


def test_multi_day_scheduler_keeps_unique_candidates_across_days():
    result = DynamicCityScheduler(meal_minutes=0, buffer_minutes=0).plan(
        city_stay=CityStay(city="杭州市", days=3),
        requirement=requirement(3),
        candidates=[
            candidate(f"A{index}", f"景点{index}", preference_score=0.9 - index / 100)
            for index in range(1, 6)
        ],
    )

    names = [name for day in result.city_plan.days for name in day.attractions]
    assert len(names) == 5
    assert len(names) == len(set(names))
    assert [day.day for day in result.city_plan.days] == [1, 2, 3]


def test_each_day_starts_with_preference_not_the_previous_day_location():
    result = DynamicCityScheduler(meal_minutes=0, buffer_minutes=0).plan(
        city_stay=CityStay(city="杭州市", days=2),
        requirement=requirement(2),
        candidates=[
            candidate("A", "第一天高偏好", preference_score=0.9, longitude=120.0, duration=720),
            candidate("B", "第二天最高剩余偏好", preference_score=0.8, longitude=120.3, duration=720),
            candidate("C", "靠近第一站", preference_score=0.79, longitude=120.001, duration=720),
        ],
    )

    assert result.city_plan.days[0].attractions == ["第一天高偏好"]
    assert result.city_plan.days[1].attractions == ["第二天最高剩余偏好"]


def _route(origin, destination, duration: int | None):
    return RouteSegment(
        origin_poi_id=origin.poi_id,
        origin_name=origin.name,
        destination_poi_id=destination.poi_id,
        destination_name=destination.name,
        mode="walking",
        source="amap_mcp" if duration is not None else "unavailable",
        distance_m=1000 if duration is not None else None,
        duration_min=duration,
    )


@pytest.mark.asyncio
async def test_route_aware_scheduler_skips_route_that_would_exceed_window():
    class RouteService:
        def __init__(self):
            self.calls = []

        async def resolve_segment(self, origin, destination, **kwargs):
            self.calls.append((origin.name, destination.name))
            duration = 180 if destination.name == "高偏好但很远" else 30
            return _route(origin, destination, duration)

    route_service = RouteService()
    result = await DynamicCityScheduler().plan_with_routes(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1, start="09:00", end="13:00"),
        candidates=[
            candidate("A", "首站", preference_score=0.9, longitude=120.0, duration=60),
            candidate("B", "高偏好但很远", preference_score=0.89, longitude=120.3, duration=120),
            candidate("C", "路线可行的备选", preference_score=0.45, longitude=120.01, duration=60),
        ],
        city_adcode="330100",
        route_service=route_service,
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
    )

    assert result.city_plan.days[0].attractions == ["首站", "路线可行的备选"]
    assert route_service.calls == [
        ("首站", "高偏好但很远"),
        ("首站", "路线可行的备选"),
    ]
    assert result.routes_by_day[0][0].duration_min == 30
    rejected = next(
        decision for decision in result.decisions if decision.name == "高偏好但很远"
    )
    assert rejected.rejected_reason == "time_window"


@pytest.mark.asyncio
async def test_route_aware_scheduler_stops_when_following_route_is_unavailable():
    class RouteService:
        async def resolve_segment(self, origin, destination, **kwargs):
            return _route(origin, destination, None)

    result = await DynamicCityScheduler().plan_with_routes(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
        candidates=[
            candidate("A", "有坐标首站", preference_score=0.9),
            candidate("B", "无可用路线", preference_score=0.8, longitude=120.01),
        ],
        city_adcode="330100",
        route_service=RouteService(),
        schedule_builder=ScheduleBuilder(),
    )

    assert result.city_plan.days[0].attractions == ["有坐标首站"]
    rejected = next(
        decision for decision in result.decisions if decision.name == "无可用路线"
    )
    assert rejected.rejected_reason == "route_unavailable"


@pytest.mark.asyncio
async def test_route_aware_scheduler_rejects_estimates_and_tries_all_candidates():
    class RouteService:
        def __init__(self):
            self.calls = []

        async def resolve_segment(self, origin, destination, **kwargs):
            self.calls.append(destination.name)
            if destination.name == "第六候选真实路线":
                return _route(origin, destination, 15)
            return RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                mode="walking",
                source="haversine",
                distance_m=1000,
                duration_min=15,
                estimated=True,
            )

    route_service = RouteService()
    result = await DynamicCityScheduler().plan_with_routes(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1, start="08:00", end="18:00"),
        candidates=[
            candidate("A", "首站", preference_score=0.99, duration=60),
            *[
                candidate(
                    f"B{index}",
                    f"第{index}候选估算路线",
                    preference_score=0.9 - index / 100,
                    longitude=120.0 + index / 1000,
                    duration=60,
                )
                for index in range(1, 6)
            ],
            candidate(
                "B6",
                "第六候选真实路线",
                preference_score=0.8,
                longitude=120.006,
                duration=60,
            ),
        ],
        city_adcode="330100",
        route_service=route_service,
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
    )

    assert result.city_plan.days[0].attractions[:2] == ["首站", "第六候选真实路线"]
    assert "第六候选真实路线" in route_service.calls
    estimated = [
        item for item in result.decisions if item.rejected_reason == "route_not_verified"
    ]
    assert len(estimated) >= 5
