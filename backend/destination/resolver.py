from pydantic import BaseModel, ConfigDict, Field

from backend.destination.repository import CatalogRepository
from backend.models.requirement import TravelRequirement


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
            }
        )
        return ResolvedDestination(
            requirement=normalized,
            province_adcode=province.adcode if province else None,
            city_adcodes=[city.adcode for city in cities],
        )

