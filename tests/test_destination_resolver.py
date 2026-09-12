import pytest

from backend.destination.resolver import DestinationResolutionError, DestinationResolver
from backend.models.requirement import TravelRequirement


def resolver(catalog_repository):
    return DestinationResolver(catalog_repository)


def test_resolves_and_completes_single_city(catalog_repository):
    result = resolver(catalog_repository).resolve(TravelRequirement(cities=["杭州"], days=1))

    assert result.requirement.province == "浙江省"
    assert result.requirement.cities == ["杭州市"]
    assert result.province_adcode == "330000"
    assert result.city_adcodes == ["330100"]


def test_resolves_same_province_cities_in_user_order(catalog_repository):
    requirement = TravelRequirement(cities=["绍兴", "杭州"], days=4, travel_scope="multi_city")

    result = resolver(catalog_repository).resolve(requirement)

    assert result.requirement.cities == ["绍兴市", "杭州市"]
    assert result.requirement.province == "浙江省"


def test_rejects_cross_province_cities(catalog_repository):
    requirement = TravelRequirement(cities=["杭州", "大理"], days=4, travel_scope="multi_city")

    with pytest.raises(DestinationResolutionError, match="跨省"):
        resolver(catalog_repository).resolve(requirement)


def test_rejects_conflicting_province_and_city(catalog_repository):
    requirement = TravelRequirement(province="云南", cities=["杭州"], days=3)

    with pytest.raises(DestinationResolutionError, match="不一致"):
        resolver(catalog_repository).resolve(requirement)


def test_rejects_unknown_city(catalog_repository):
    with pytest.raises(DestinationResolutionError, match="无法识别城市"):
        resolver(catalog_repository).resolve(TravelRequirement(cities=["不存在"] , days=1))
