import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.evaluator import PlanEvaluator
from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
from backend.models.retrieval import HybridSearchResult
from backend.planner.workflow import DestinationChoiceRequired, TravelPlanningWorkflow
from backend.requirements import RequirementParser
from backend.schedule import ScheduleBuilder


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
        attractions = [
            HybridSearchResult(
                poi_id=f"{city_stay.city}-poi-{day}",
                name=f"{city_stay.city}景点{day}",
                city=city_stay.city,
                category="风景名胜",
                longitude=120.0 + day / 100,
                latitude=30.0 + day / 100,
                description=f"{city_stay.city}第{day}天的测试景点资料。",
                rrf_score=1 / day,
                final_rank=day,
            )
            for day in range(1, city_stay.days + 1)
        ]
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=attractions,
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


class RouteService:
    def __init__(self):
        self.days = []

    async def enrich_day(self, day, *, requested_mode):
        self.days.append((day.day, requested_mode))
        return day


class WeatherService:
    def __init__(self):
        self.days = []

    async def enrich_day(self, day):
        self.days.append(day.day)
        return day


@pytest.mark.asyncio
async def test_yunnan_workflow_reuses_city_planner_and_flattens_seven_days(catalog_repository):
    bootstrap = Bootstrap()
    city_service = CityService()
    route_service = RouteService()
    weather_service = WeatherService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=bootstrap,
        city_planning_service=city_service,
        route_enrichment_service=route_service,
        weather_enrichment_service=weather_service,
        schedule_builder=ScheduleBuilder(),
        evaluator=PlanEvaluator(),
    )

    result = await workflow.run("云南玩七天，想去两三个城市，喜欢自然、古城和摄影")

    assert [(stay.city, stay.days) for stay in result.plan.city_stays] == [
        ("昆明市", 3),
        ("大理市", 2),
        ("丽江市", 2),
    ]
    assert bootstrap.cities == ["昆明市", "大理市", "丽江市"]
    assert len(city_service.stays) == 3
    assert [day.day for day in result.plan.itinerary] == list(range(1, 8))
    assert [day.city for day in result.plan.itinerary] == [
        "昆明市",
        "昆明市",
        "昆明市",
        "大理市",
        "大理市",
        "丽江市",
        "丽江市",
    ]
    assert all(day.attractions[0].poi_id for day in result.plan.itinerary)
    assert route_service.days == [(day, "auto") for day in range(1, 8)]
    assert weather_service.days == list(range(1, 8))
    assert all(day.schedule is not None for day in result.plan.itinerary)
    assert all(day.attractions[0].start_time == "08:00" for day in result.plan.itinerary)
    assert result.plan.evaluation is not None


@pytest.mark.asyncio
async def test_auto_scope_directly_selects_multi_city_after_three_days(catalog_repository):
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
        route_enrichment_service=RouteService(),
        weather_enrichment_service=WeatherService(),
        schedule_builder=ScheduleBuilder(),
        evaluator=PlanEvaluator(),
    )

    result = await workflow.run("云南玩七天，喜欢自然、古城和摄影")

    assert len(result.plan.city_stays) == 3
    assert sum(stay.days for stay in result.plan.city_stays) == 7


class IssueEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, plan, requirement):
        self.calls += 1
        return EvaluationResult(
            passed=False,
            issues=[EvaluationIssue(
                type="schedule_density", severity="high", day=1,
                message="第 1 天行程过密", suggestion="请人工调整景点",
            )],
        )


@pytest.mark.asyncio
async def test_workflow_evaluates_once_and_keeps_issues_for_user(catalog_repository):
    evaluator = IssueEvaluator()
    route_service = RouteService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
        route_enrichment_service=route_service,
        weather_enrichment_service=WeatherService(),
        schedule_builder=ScheduleBuilder(),
        evaluator=evaluator,
    )

    result = await workflow.run("杭州玩一天，喜欢自然", travel_scope="single_city")

    assert evaluator.calls == 1
    assert route_service.days == [(1, "auto")]
    assert result.plan.evaluation.issues[0].message == "第 1 天行程过密"
