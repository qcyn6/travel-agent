import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
from backend.planner.workflow import DestinationChoiceRequired, TravelPlanningWorkflow
from backend.requirements import RequirementParser


class Bootstrap:
    def __init__(self):
        self.cities = []

    async def ensure(self, *, city, **kwargs):
        self.cities.append(city.name)


class CityService:
    def __init__(self):
        self.stays = []

    async def plan(self, *, city_stay, requirement, top_k):
        self.stays.append(city_stay)
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=[],
            city_plan=CityPlan(
                city=city_stay.city,
                days=[
                    DayPlan(
                        day=day,
                        attractions=[f"{city_stay.city}景点{day}"],
                        description=f"{city_stay.city}第{day}天的合理城市行程安排。",
                    )
                    for day in range(1, city_stay.days + 1)
                ],
            ),
        )


@pytest.mark.asyncio
async def test_yunnan_workflow_reuses_city_planner_and_flattens_seven_days(catalog_repository):
    bootstrap = Bootstrap()
    city_service = CityService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=bootstrap,
        city_planning_service=city_service,
    )

    result = await workflow.run("云南玩七天，想去两三个城市，喜欢自然、古城和摄影")

    assert [(stay.city, stay.days) for stay in result.plan.city_stays] == [
        ("昆明市", 1),
        ("大理市", 3),
        ("丽江市", 3),
    ]
    assert bootstrap.cities == ["昆明市", "大理市", "丽江市"]
    assert len(city_service.stays) == 3
    assert [day.day for day in result.plan.itinerary] == list(range(1, 8))
    assert [day.city for day in result.plan.itinerary] == [
        "昆明市",
        "大理市",
        "大理市",
        "大理市",
        "丽江市",
        "丽江市",
        "丽江市",
    ]


@pytest.mark.asyncio
async def test_auto_scope_requires_user_choice(catalog_repository):
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
    )

    with pytest.raises(DestinationChoiceRequired):
        await workflow.run("云南玩七天，喜欢自然、古城和摄影")
