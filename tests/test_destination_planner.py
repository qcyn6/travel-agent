import pytest

from backend.destination.planner import DestinationPlanner, DestinationPlanningError
from backend.models.requirement import TravelRequirement


def test_single_specified_city_receives_all_days(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="浙江省",
            cities=["杭州市"],
            days=3,
            interests=["自然"],
            travel_scope="single_city",
        )
    )

    assert [(stay.city, stay.days) for stay in result.city_stays] == [("杭州市", 3)]


def test_specified_city_order_is_preserved(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="浙江省",
            cities=["绍兴市", "杭州市"],
            days=4,
            interests=["历史文化"],
            travel_scope="multi_city",
        )
    )

    assert [stay.city for stay in result.city_stays] == ["绍兴市", "杭州市"]
    assert sum(stay.days for stay in result.city_stays) == 4


def test_yunnan_seven_days_selects_three_cities_without_city_day_profiles(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=7,
            interests=["自然", "古城", "摄影"],
            travel_scope="multi_city",
        )
    )

    assert [stay.city for stay in result.city_stays] == ["昆明市", "大理市", "丽江市"]
    assert sorted(stay.days for stay in result.city_stays) == [2, 2, 3]
    assert sum(stay.days for stay in result.city_stays) == 7


def test_auto_scope_uses_multi_city_for_more_than_three_days(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=7,
            interests=["自然", "古城", "摄影"],
            travel_scope="auto",
        )
    )

    assert result.requires_choice is False
    assert result.alternative_city_stays is None
    assert len(result.city_stays) == 3
    assert sum(stay.days for stay in result.city_stays) == 7


def test_four_day_auto_scope_never_falls_back_to_single_city(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=4,
            interests=["自然"],
            travel_scope="auto",
        )
    )

    assert len(result.city_stays) >= 2
    assert sum(stay.days for stay in result.city_stays) == 4


def test_infeasible_explicit_combination_is_rejected(catalog_repository):
    with pytest.raises(ValueError, match="至少"):
        TravelRequirement(
            province="云南省",
            cities=["大理市", "丽江市"],
            days=1,
            travel_scope="multi_city",
        )


def test_single_city_is_limited_to_three_days(catalog_repository):
    with pytest.raises(ValueError, match="单城市规划目前只支持1～3天"):
        TravelRequirement(
            province="云南省",
            cities=["大理市"],
            days=4,
            travel_scope="single_city",
        )


def test_planner_keeps_multi_city_guard_for_constructed_requirements(catalog_repository):
    requirement = TravelRequirement.model_construct(
        province="云南省",
        cities=["大理市", "丽江市"],
        days=1,
        travel_scope="multi_city",
        interests=[],
    )
    with pytest.raises(DestinationPlanningError, match="至少"):
        DestinationPlanner(catalog_repository).plan(requirement)


def test_missing_destination_recommends_by_province_and_city_tags(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            days=3,
            interests=["古城", "摄影"],
            travel_scope="auto",
        )
    )

    assert [(stay.city, stay.days) for stay in result.city_stays] == [("大理市", 3)]
