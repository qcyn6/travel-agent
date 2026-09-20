import pytest

from backend.models.city_stay import CityStay
from backend.models.planning import PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.planner.dynamic_scheduler import (
    DynamicCityScheduler,
    calculate_distance_score,
    calculate_priority,
    haversine_distance_m,
)


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
