import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.evaluator import PlanEvaluator
from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
from backend.models.retrieval import HybridSearchResult
from backend.models.replanning import ReplanningResult
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


class FactsService:
    def __init__(self):
        self.days = []

    async def enrich_day(self, day):
        self.days.append(day.day)
        return day


class NoopReplanningService:
    async def replan(self, **kwargs):
        raise AssertionError("没有高等级问题时不应调用 Replanner")


@pytest.mark.asyncio
async def test_yunnan_workflow_reuses_city_planner_and_flattens_seven_days(catalog_repository):
    bootstrap = Bootstrap()
    city_service = CityService()
    route_service = RouteService()
    weather_service = WeatherService()
    facts_service = FactsService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=bootstrap,
        city_planning_service=city_service,
        route_enrichment_service=route_service,
        weather_enrichment_service=weather_service,
        attraction_facts_service=facts_service,
        schedule_builder=ScheduleBuilder(),
        evaluator=PlanEvaluator(),
        replanning_service=NoopReplanningService(),
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
    assert all(day.attractions[0].poi_id for day in result.plan.itinerary)
    assert route_service.days == [(day, "auto") for day in range(1, 8)]
    assert weather_service.days == list(range(1, 8))
    assert facts_service.days == list(range(1, 8))
    assert all(day.schedule is not None for day in result.plan.itinerary)
    assert all(day.attractions[0].start_time == "09:00" for day in result.plan.itinerary)
    assert result.plan.evaluation is not None


@pytest.mark.asyncio
async def test_auto_scope_requires_user_choice(catalog_repository):
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
        route_enrichment_service=RouteService(),
        weather_enrichment_service=WeatherService(),
        attraction_facts_service=FactsService(),
        schedule_builder=ScheduleBuilder(),
        evaluator=PlanEvaluator(),
        replanning_service=NoopReplanningService(),
    )

    with pytest.raises(DestinationChoiceRequired):
        await workflow.run("云南玩七天，喜欢自然、古城和摄影")


class SequenceEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, plan, requirement):
        self.calls += 1
        if self.calls == 1:
            return EvaluationResult(
                passed=False,
                replan_required=True,
                issues=[EvaluationIssue(
                    type="schedule_density", severity="high", day=1,
                    message="第 1 天过密", suggestion="减少景点",
                )],
            )
        return EvaluationResult(passed=True, replan_required=False, issues=[])


class AlwaysFailEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, plan, requirement):
        self.calls += 1
        return EvaluationResult(
            passed=False,
            replan_required=True,
            issues=[EvaluationIssue(
                type="schedule_density", severity="high", day=1,
                message="第 1 天仍然过密", suggestion="继续减少景点",
            )],
        )


class LocalReplanningService:
    def __init__(self):
        self.calls = 0

    async def replan(self, *, plan, **kwargs):
        self.calls += 1
        return ReplanningResult(plan=plan, changed_days=[1])


@pytest.mark.asyncio
async def test_workflow_replans_and_refreshes_only_affected_day(catalog_repository):
    route_service = RouteService()
    facts_service = FactsService()
    evaluator = SequenceEvaluator()
    replanning_service = LocalReplanningService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
        route_enrichment_service=route_service,
        weather_enrichment_service=WeatherService(),
        attraction_facts_service=facts_service,
        schedule_builder=ScheduleBuilder(),
        evaluator=evaluator,
        replanning_service=replanning_service,
        max_replan_count=2,
    )

    result = await workflow.run("杭州玩一天，喜欢自然", travel_scope="single_city")

    assert replanning_service.calls == 1
    assert evaluator.calls == 2
    assert route_service.days == [(1, "auto"), (1, "auto")]
    assert facts_service.days == [1, 1]
    assert result.plan.replan_count == 1
    assert result.plan.replan_limit_reached is False


@pytest.mark.asyncio
async def test_langgraph_stops_after_configured_replan_limit(catalog_repository):
    evaluator = AlwaysFailEvaluator()
    replanning_service = LocalReplanningService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=CityService(),
        route_enrichment_service=RouteService(),
        weather_enrichment_service=WeatherService(),
        attraction_facts_service=FactsService(),
        schedule_builder=ScheduleBuilder(),
        evaluator=evaluator,
        replanning_service=replanning_service,
        max_replan_count=2,
    )

    result = await workflow.run("杭州玩一天，喜欢自然", travel_scope="single_city")

    assert replanning_service.calls == 2
    assert evaluator.calls == 3
    assert result.plan.replan_count == 2
    assert result.plan.replan_limit_reached is True
    assert result.plan.evaluation.replan_required is True
