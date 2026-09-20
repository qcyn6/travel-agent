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


def test_rewrites_interest_to_exact_catalog_tag(catalog_repository):
    result = resolver(catalog_repository).resolve(
        TravelRequirement(cities=["杭州"], days=1, interests=["自然风光"])
    )

    assert result.requirement.interests == ["自然"]


def test_drops_interest_that_is_not_in_catalog_tag_vocabulary(catalog_repository):
    result = resolver(catalog_repository).resolve(
        TravelRequirement(cities=["杭州"], days=1, interests=["不存在的兴趣"])
    )

    assert result.requirement.interests == []


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


def test_rejects_more_than_three_cities(catalog_repository):
    requirement = TravelRequirement(
        cities=["杭州", "绍兴", "宁波", "温州"],
        days=7,
        travel_scope="multi_city",
    )

    with pytest.raises(DestinationResolutionError, match="最多支持3个城市"):
        resolver(catalog_repository).resolve(requirement)


def test_rejects_city_scope_conflict(catalog_repository):
    with pytest.raises(DestinationResolutionError, match="1个城市"):
        resolver(catalog_repository).resolve(
            TravelRequirement(cities=["杭州"], days=3, travel_scope="multi_city")
        )

    with pytest.raises(DestinationResolutionError, match="多个城市"):
        resolver(catalog_repository).resolve(
            TravelRequirement(
                cities=["杭州", "绍兴"],
                days=3,
                travel_scope="single_city",
            )
        )


def test_resolver_rejects_short_auto_multi_city_requirement(catalog_repository):
    requirement = TravelRequirement.model_construct(
        cities=["杭州", "绍兴"],
        days=3,
        travel_scope="auto",
        interests=[],
    )

    with pytest.raises(DestinationResolutionError, match="只支持单城市"):
        resolver(catalog_repository).resolve(requirement)


@pytest.mark.parametrize(
    ("province", "city", "canonical_province", "canonical_city", "city_adcode"),
    [
        ("北京", "北京", "北京市", "北京市", "110000"),
        ("四川", "成都", "四川省", "成都市", "510100"),
        ("广西", "桂林", "广西壮族自治区", "桂林市", "450300"),
        ("新疆", "乌鲁木齐", "新疆维吾尔自治区", "乌鲁木齐市", "650100"),
    ],
)
def test_resolves_nationwide_catalog_aliases(
    catalog_repository,
    province,
    city,
    canonical_province,
    canonical_city,
    city_adcode,
):
    result = resolver(catalog_repository).resolve(
        TravelRequirement(province=province, cities=[city], days=3)
    )

    assert result.requirement.province == canonical_province
    assert result.requirement.cities == [canonical_city]
    assert result.city_adcodes == [city_adcode]
