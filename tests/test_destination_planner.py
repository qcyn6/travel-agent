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


def test_multi_city_recommendation_skips_single_city_province_regions(
    catalog_repository,
):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            days=6,
            interests=["历史文化", "摄影"],
            travel_scope="multi_city",
        )
    )

    assert len(result.city_stays) >= 2
    assert all(stay.city != "北京市" for stay in result.city_stays)
    assert sum(stay.days for stay in result.city_stays) == 6


def test_explicit_single_city_province_region_has_clear_multi_city_error(
    catalog_repository,
):
    with pytest.raises(DestinationPlanningError, match="单城市型省级地区"):
        DestinationPlanner(catalog_repository).plan(
            TravelRequirement(
                province="北京市",
                days=6,
                interests=["历史文化", "摄影"],
                travel_scope="multi_city",
            )
        )


def test_destination_ranking_uses_same_six_interest_groups_as_attractions(
    catalog_repository,
):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=3,
            interests=["古城"],
            travel_scope="single_city",
        )
    )

    assert result.city_stays[0].city == "大理市"
