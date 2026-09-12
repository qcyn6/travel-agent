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


def test_yunnan_seven_days_selects_three_feasible_cities(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=7,
            interests=["自然", "古城", "摄影"],
            travel_scope="multi_city",
        )
    )

    assert [(stay.city, stay.days) for stay in result.city_stays] == [
        ("昆明市", 1),
        ("大理市", 3),
        ("丽江市", 3),
    ]


def test_auto_scope_returns_single_and_multi_city_options(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            province="云南省",
            days=7,
            interests=["自然", "古城", "摄影"],
            travel_scope="auto",
        )
    )

    assert result.requires_choice is True
    assert len(result.city_stays) == 1
    assert result.alternative_city_stays is not None
    assert len(result.alternative_city_stays) == 3


def test_infeasible_explicit_combination_is_rejected(catalog_repository):
    with pytest.raises(DestinationPlanningError, match="建议天数"):
        DestinationPlanner(catalog_repository).plan(
            TravelRequirement(
                province="云南省",
                cities=["大理市", "丽江市"],
                days=2,
                travel_scope="multi_city",
            )
        )


def test_missing_destination_recommends_by_province_and_city_tags(catalog_repository):
    result = DestinationPlanner(catalog_repository).plan(
        TravelRequirement(
            days=3,
            interests=["古城", "摄影"],
            travel_scope="auto",
        )
    )

    assert [(stay.city, stay.days) for stay in result.city_stays] == [("大理市", 3)]
