from pydantic import BaseModel, ConfigDict, Field

from backend.destination.repository import CatalogRepository
from backend.models.requirement import (
    MAX_TRIP_CITIES,
    TravelRequirement,
    validate_scope_day_boundary,
)
from backend.tag_matching import normalize_interest_tags


class DestinationResolutionError(ValueError):
    """Raised when a destination cannot enter the supported MVP workflow."""


class ResolvedDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: TravelRequirement
    province_adcode: str | None = None
    city_adcodes: list[str] = Field(default_factory=list)


class DestinationResolver:
    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    def resolve(self, requirement: TravelRequirement) -> ResolvedDestination:
        normalized_interests, _ = self._normalize_interests(requirement.interests)
        province = None
        if requirement.province:
            province = self.repository.find_province(requirement.province)
            if province is None:
                raise DestinationResolutionError(f"暂不支持或无法识别省份：{requirement.province}")

        cities = []
        for city_name in requirement.cities:
            city = self.repository.find_city(city_name)
            if city is None:
                raise DestinationResolutionError(f"暂不支持或无法识别城市：{city_name}")
            cities.append(city)

        if len(cities) > MAX_TRIP_CITIES:
            raise DestinationResolutionError(
                f"一次规划最多支持{MAX_TRIP_CITIES}个城市，请选择同一省份内的1～{MAX_TRIP_CITIES}个城市"
            )

        if len(cities) == 1 and requirement.travel_scope == "multi_city":
            raise DestinationResolutionError(
                "当前只指定了1个城市，城市模式请选择单城市深度游或由 AI 推荐"
            )
        if len(cities) > 1 and requirement.travel_scope == "single_city":
            raise DestinationResolutionError(
                "当前指定了多个城市，城市模式请选择同省多城市或由 AI 推荐"
            )

        try:
            validate_scope_day_boundary(
                requirement.days,
                requirement.travel_scope,
                city_count=len(cities),
            )
        except ValueError as exc:
            raise DestinationResolutionError(str(exc)) from exc

        province_ids = {city.province_id for city in cities}
        if len(province_ids) > 1:
            raise DestinationResolutionError("MVP 暂不支持跨省多城市规划，请选择同一省份内的城市")

        if province and province_ids and province.id not in province_ids:
            raise DestinationResolutionError("指定省份与城市不一致")

        if not province and cities:
            province = cities[0].province

        normalized = requirement.model_copy(
            update={
                "province": province.name if province else None,
                "cities": [city.name for city in cities],
                "interests": normalized_interests,
            }
        )
        return ResolvedDestination(
            requirement=normalized,
            province_adcode=province.adcode if province else None,
            city_adcodes=[city.adcode for city in cities],
        )

    def _normalize_interests(self, interests: list[str]) -> tuple[list[str], list[str]]:
        """Normalize against the live repository, with test-double fallback."""

        normalizer = getattr(self.repository, "normalize_interest_tags", None)
        if callable(normalizer):
            return normalizer(interests)

        # Keep lightweight repository doubles and older integrations working.
        # The production SQL repository takes the faster query-based path
        # above.
        tags: list[str] = []
        for province in self.repository.list_provinces():
            tags.extend(province.tags or [])
            for city in self.repository.list_cities_by_province(province.name):
                tags.extend(city.tags or [])
        return normalize_interest_tags(interests, tags)
