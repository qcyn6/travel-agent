from itertools import combinations

from backend.models.city_stay import CityStay
from backend.models.destination import DestinationPlanResult
from backend.models.requirement import (
    MAX_SINGLE_CITY_DAYS,
    MAX_TRAVEL_DAYS,
    MAX_TRIP_CITIES,
    MIN_MULTI_CITY_DAYS,
    MIN_MULTI_CITY_TRIP_DAYS,
    TravelRequirement,
    validate_scope_day_boundary,
)
from backend.tag_matching import (
    compact_tag_match_score,
    normalize_interest_tags,
    tag_match_score,
)


class DestinationPlanningError(ValueError):
    pass


class DestinationPlanner:
    def __init__(self, repository):
        self.repository = repository

    def plan(self, requirement: TravelRequirement) -> DestinationPlanResult:
        interests = self._normalize_interests(requirement.interests)
        if not requirement.days:
            raise DestinationPlanningError("缺少旅行总天数")
        if not MIN_MULTI_CITY_DAYS <= requirement.days <= MAX_TRAVEL_DAYS:
            raise DestinationPlanningError(
                f"总行程目前只支持{MIN_MULTI_CITY_DAYS}～{MAX_TRAVEL_DAYS}天"
            )
        try:
            validate_scope_day_boundary(
                requirement.days,
                requirement.travel_scope,
                city_count=len(requirement.cities),
            )
        except ValueError as exc:
            raise DestinationPlanningError(str(exc)) from exc
        if requirement.days > MAX_SINGLE_CITY_DAYS and len(requirement.cities) == 1:
            raise DestinationPlanningError(
                f"单城市规划目前只支持{MIN_MULTI_CITY_DAYS}～{MAX_SINGLE_CITY_DAYS}天；"
                f"{MIN_MULTI_CITY_TRIP_DAYS}～{MAX_TRAVEL_DAYS}天必须改为同省多城市"
            )
        if requirement.cities:
            cities = [self._require_city(name) for name in requirement.cities]
            if len(cities) == 1:
                self._validate_single_city_days(requirement.days)
                return DestinationPlanResult(
                    city_stays=[CityStay(city=cities[0].name, days=requirement.days)]
                )
            return DestinationPlanResult(
                city_stays=self._allocate(cities, requirement.days, interests)
            )
        province_name = requirement.province
        candidates = None
        requires_multi_city = requirement.travel_scope == "multi_city" or (
            requirement.travel_scope == "auto"
            and requirement.days > MAX_SINGLE_CITY_DAYS
        )
        if not province_name:
            provinces = self.repository.list_provinces()
            if not provinces:
                raise DestinationPlanningError("暂无可推荐的省份")
            province_candidates = []
            for province in provinces:
                province_cities = self.repository.list_cities_by_province(
                    province.name
                )
                if requires_multi_city and len(province_cities) < 2:
                    # Municipalities and special administrative regions are
                    # valid single-city destinations, but cannot form the
                    # same-province multi-city itinerary required for 4–7 days.
                    continue
                province_candidates.append((province, province_cities))
            if not province_candidates:
                raise DestinationPlanningError(
                    "暂无至少包含2个可规划城市的省份，无法安排同省多城市行程"
                )
            province, candidates = max(
                province_candidates,
                key=lambda item: (
                    compact_tag_match_score(item[0].tags, interests),
                    tag_match_score(item[0].tags, interests),
                    -item[0].id,
                ),
            )
            province_name = province.name

        if candidates is None:
            candidates = self.repository.list_cities_by_province(province_name)
        if not candidates:
            raise DestinationPlanningError(f"{province_name} 暂无候选城市")
        if requires_multi_city and len(candidates) < 2:
            raise DestinationPlanningError(
                f"{province_name}属于单城市型省级地区，当前只支持1～3天单城市规划；"
                "4～7天请选择至少包含2个城市的省份"
            )
        ranked = sorted(
            candidates,
            key=lambda city: (
                -self._match_score(city, interests),
                -self._exact_match_score(city, interests),
                city.id,
            ),
        )
        if requirement.travel_scope == "single_city" or (
            requirement.travel_scope == "auto" and requirement.days <= MAX_SINGLE_CITY_DAYS
        ):
            self._validate_single_city_days(requirement.days)
            return DestinationPlanResult(
                city_stays=[CityStay(city=ranked[0].name, days=requirement.days)]
            )

        multi = self._choose_multi_city(
            ranked, requirement.days, interests
        )
        return DestinationPlanResult(city_stays=multi)

    def _choose_multi_city(self, ranked, total_days: int, interests: list[str]) -> list[CityStay]:
        for size in (3, 2):
            if total_days < size:
                continue
            feasible = list(combinations(ranked, size))
            if feasible:
                best = max(
                    feasible,
                    key=lambda group: (
                        sum(self._match_score(city, interests) for city in group),
                        sum(self._exact_match_score(city, interests) for city in group),
                        -sum(city.id for city in group),
                    ),
                )
                selected = sorted(best, key=lambda city: city.id)
                return self._allocate(selected, total_days, interests)
        raise DestinationPlanningError(
            "多城市规划至少需要为每个城市安排1天：2个城市至少2天，3个城市至少3天"
        )

    def _allocate(self, cities, total_days: int, interests: list[str]) -> list[CityStay]:
        if not 2 <= len(cities) <= MAX_TRIP_CITIES:
            raise DestinationPlanningError(
                f"多城市规划只支持同省2～{MAX_TRIP_CITIES}个城市"
            )
        province_ids = {city.province_id for city in cities}
        if len(province_ids) != 1:
            raise DestinationPlanningError("多城市规划只支持同一省份")
        if total_days < len(cities):
            raise DestinationPlanningError(
                "多城市规划至少需要为每个城市安排1天：2个城市至少2天，3个城市至少3天"
            )
        if total_days > MAX_TRAVEL_DAYS:
            raise DestinationPlanningError(
                f"多城市规划目前只支持{MIN_MULTI_CITY_DAYS}～{MAX_TRAVEL_DAYS}天"
            )

        # City profiles no longer carry hidden day ranges. Every selected city
        # receives one day first; remaining days are distributed evenly so a
        # longer trip cannot silently turn into a single-city plan.
        allocated = [1] * len(cities)
        for offset in range(total_days - len(cities)):
            allocated[offset % len(cities)] += 1
        return [CityStay(city=city.name, days=days) for city, days in zip(cities, allocated, strict=True)]

    @staticmethod
    def _validate_single_city_days(total_days: int) -> None:
        if not MIN_MULTI_CITY_DAYS <= total_days <= MAX_SINGLE_CITY_DAYS:
            raise DestinationPlanningError(
                f"单城市规划目前只支持{MIN_MULTI_CITY_DAYS}～{MAX_SINGLE_CITY_DAYS}天；"
                "超过3天请改为同省多城市"
            )

    def _require_city(self, name: str):
        city = self.repository.find_city(name)
        if city is None:
            raise DestinationPlanningError(f"暂不支持或无法识别城市：{name}")
        return city

    @staticmethod
    def _match_score(city, interests: list[str]) -> int:
        return compact_tag_match_score(city.tags, interests)

    @staticmethod
    def _exact_match_score(city, interests: list[str]) -> int:
        return tag_match_score(city.tags, interests)

    def _normalize_interests(self, interests: list[str]) -> list[str]:
        normalizer = getattr(self.repository, "normalize_interest_tags", None)
        if callable(normalizer):
            normalized, _ = normalizer(interests)
            return normalized
        tags: list[str] = []
        for province in self.repository.list_provinces():
            tags.extend(province.tags or [])
            for city in self.repository.list_cities_by_province(province.name):
                tags.extend(city.tags or [])
        normalized, _ = normalize_interest_tags(interests, tags)
        return normalized
