from itertools import combinations

from pydantic import BaseModel, ConfigDict, Field

from backend.models.city_stay import CityStay
from backend.models.requirement import TravelRequirement


class DestinationPlanningError(ValueError):
    pass


class DestinationPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city_stays: list[CityStay] = Field(min_length=1, max_length=3)
    alternative_city_stays: list[CityStay] | None = None
    requires_choice: bool = False


class DestinationPlanner:
    def __init__(self, repository):
        self.repository = repository

    def plan(self, requirement: TravelRequirement) -> DestinationPlanResult:
        if not requirement.days:
            raise DestinationPlanningError("缺少旅行总天数")
        if requirement.cities:
            cities = [self._require_city(name) for name in requirement.cities]
            if len(cities) == 1:
                return DestinationPlanResult(
                    city_stays=[CityStay(city=cities[0].name, days=requirement.days)]
                )
            return DestinationPlanResult(
                city_stays=self._allocate(cities, requirement.days, requirement.interests)
            )
        province_name = requirement.province
        if not province_name:
            provinces = self.repository.list_provinces()
            if not provinces:
                raise DestinationPlanningError("暂无可推荐的省份")
            province = max(
                provinces,
                key=lambda item: (
                    len(set(requirement.interests) & set(item.tags or [])),
                    -item.id,
                ),
            )
            province_name = province.name

        candidates = self.repository.list_cities_by_province(province_name)
        if not candidates:
            raise DestinationPlanningError(f"{province_name} 暂无候选城市")
        ranked = sorted(
            candidates,
            key=lambda city: (-self._match_score(city, requirement.interests), city.id),
        )
        single = [CityStay(city=ranked[0].name, days=requirement.days)]
        if requirement.travel_scope == "single_city" or (
            requirement.travel_scope == "auto" and requirement.days <= 3
        ):
            return DestinationPlanResult(city_stays=single)

        multi = self._choose_multi_city(
            ranked, requirement.days, requirement.interests
        )
        if requirement.travel_scope == "auto":
            return DestinationPlanResult(
                city_stays=single,
                alternative_city_stays=multi,
                requires_choice=True,
            )
        return DestinationPlanResult(city_stays=multi)

    def _choose_multi_city(self, ranked, total_days: int, interests: list[str]) -> list[CityStay]:
        for size in (3, 2):
            feasible = [
                group
                for group in combinations(ranked, size)
                if sum(city.suitable_days_min for city in group) <= total_days
                <= sum(city.suitable_days_max for city in group)
            ]
            if feasible:
                best = max(
                    feasible,
                    key=lambda group: (
                        sum(self._match_score(city, interests) for city in group),
                        -sum(city.id for city in group),
                    ),
                )
                selected = sorted(best, key=lambda city: city.id)
                return self._allocate(selected, total_days, interests)
        raise DestinationPlanningError("总天数与同省 2～3 个候选城市的建议天数不匹配")

    def _allocate(self, cities, total_days: int, interests: list[str]) -> list[CityStay]:
        if not 2 <= len(cities) <= 3:
            raise DestinationPlanningError("多城市 MVP 只支持同省 2～3 个城市")
        province_ids = {city.province_id for city in cities}
        if len(province_ids) != 1:
            raise DestinationPlanningError("多城市 MVP 只支持同一省份")
        minimum = sum(city.suitable_days_min for city in cities)
        maximum = sum(city.suitable_days_max for city in cities)
        if not minimum <= total_days <= maximum:
            raise DestinationPlanningError("总天数不满足所选城市组合的建议天数范围")

        allocated = [city.suitable_days_min for city in cities]
        scores = [self._match_score(city, interests) for city in cities]
        for _ in range(total_days - minimum):
            available = [index for index, city in enumerate(cities) if allocated[index] < city.suitable_days_max]
            chosen = max(
                available,
                key=lambda index: (scores[index] / (1 + allocated[index] - cities[index].suitable_days_min), -index),
            )
            allocated[chosen] += 1
        return [CityStay(city=city.name, days=days) for city, days in zip(cities, allocated, strict=True)]

    def _require_city(self, name: str):
        city = self.repository.find_city(name)
        if city is None:
            raise DestinationPlanningError(f"暂不支持或无法识别城市：{name}")
        return city

    @staticmethod
    def _match_score(city, interests: list[str]) -> int:
        return len(set(interests) & set(city.tags or []))
