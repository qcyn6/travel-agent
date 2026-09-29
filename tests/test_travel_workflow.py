import asyncio

import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.evaluator import PlanEvaluator
from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.hotel import HotelStay
from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
from backend.models.retrieval import HybridSearchResult
from backend.models.route import RouteSegment
from backend.planner.city import CityPlanningError
from backend.planner.service import CityPlanningService, RouteAwareCityPlanningResult
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
        self.top_ks = []

    async def plan(self, *, city_stay, requirement, top_k):
        self.stays.append(city_stay)
        self.top_ks.append(top_k)
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


class RouteAwareRouteService(RouteService):
    def __init__(self):
        super().__init__()
        self.resolve_calls = []

    async def resolve_segment(self, origin, destination, **kwargs):
        self.resolve_calls.append((origin.name, destination.name))
        return RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            mode="walking",
            source="amap_mcp",
            distance_m=1000,
            duration_min=15,
        )

    async def enrich_day(self, day, *, requested_mode):
        raise AssertionError("路线感知排程完成后不应重复调用 enrich_day")


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

    class OvernightHotels:
        def __init__(self):
            self.connections = []

        async def enrich_plan(self, plan, *, requested_mode):
            days = [
                day.model_copy(update={
                    "hotel": HotelStay(
                        poi_id=f"hotel-{day.day}",
                        name=f"{day.city}酒店",
                        city=day.city,
                    )
                })
                for day in plan.itinerary
            ]
            return plan.model_copy(update={"itinerary": days})

        async def connect_overnight(self, previous_hotel, day, **kwargs):
            self.connections.append((previous_hotel.city, day.city))
            return day.model_copy(update={"start_hotel": previous_hotel})

    hotels = OvernightHotels()
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
        hotel_enrichment_service=hotels,
    )

    result = await workflow.run("云南玩七天，想去两三个城市，喜欢自然、古城和摄影")

    assert [(stay.city, stay.days) for stay in result.plan.city_stays] == [
        ("昆明市", 3),
        ("大理市", 2),
        ("丽江市", 2),
    ]
    assert bootstrap.cities == ["昆明市", "大理市", "丽江市"]
    assert len(city_service.stays) == 3
    assert city_service.top_ks == [20, 20, 20]
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
    assert hotels.connections == [
        ("昆明市", "昆明市"),
        ("昆明市", "昆明市"),
        ("大理市", "大理市"),
        ("丽江市", "丽江市"),
    ]
    assert result.plan.itinerary[3].start_hotel is None
    assert result.plan.itinerary[5].start_hotel is None
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


@pytest.mark.asyncio
async def test_workflow_reuses_routes_validated_before_attraction_selection(catalog_repository):
    route_service = RouteAwareRouteService()
    progress_events = []

    class DescriptionService:
        def __init__(self):
            self.calls = 0

        async def enrich(self, *, candidates, **kwargs):
            self.calls += 1
            return candidates

    class PhotoService:
        async def enrich(self, *, candidates, **kwargs):
            return {
                item.poi_id: f"https://example.com/{item.poi_id}.jpg"
                for item in candidates
            }

    class Retrieval:
        async def search(self, **kwargs):
            return [
                HybridSearchResult(
                    poi_id="A1",
                    name="西湖",
                    city="杭州市",
                    category="景区",
                    longitude=120.1,
                    latitude=30.2,
                    description="西湖的测试景点资料。",
                    preference_score=0.9,
                    keyword_score=0.9,
                    preference_similarity=0.9,
                    rrf_score=0.9,
                    final_rank=1,
                ),
                HybridSearchResult(
                    poi_id="A2",
                    name="灵隐寺",
                    city="杭州市",
                    category="景区",
                    longitude=120.11,
                    latitude=30.21,
                    description="灵隐寺的测试景点资料。",
                    preference_score=0.8,
                    keyword_score=0.8,
                    preference_similarity=0.8,
                    rrf_score=0.8,
                    final_rank=2,
                ),
            ]

    class Narrator:
        async def plan(self, **kwargs):
            raise CityPlanningError("测试中跳过 LLM 说明")

    city_service = CityPlanningService(
        retrieval_service=Retrieval(),
        planner=Narrator(),
        route_enrichment_service=route_service,
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
    )
    description_service = DescriptionService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=city_service,
        route_enrichment_service=route_service,
        weather_enrichment_service=WeatherService(),
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
        evaluator=PlanEvaluator(),
        attraction_description_service=description_service,
        attraction_photo_service=PhotoService(),
    )

    requirement = RequirementParser().parse("杭州玩一天，喜欢自然").model_copy(
        update={"travel_scope": "single_city"}
    )

    async def progress(step, label, status):
        progress_events.append((step, label, status))

    result = await workflow.run_requirement(requirement, progress=progress)

    assert route_service.resolve_calls == [("西湖", "灵隐寺")]
    assert route_service.days == []
    assert len(result.plan.itinerary[0].routes) == 1
    assert result.plan.itinerary[0].routes[0].source == "amap_mcp"
    assert result.plan.itinerary[0].attractions[0].image_url == "https://example.com/A1.jpg"
    assert result.plan.itinerary[0].schedule is not None
    assert description_service.calls == 0
    retrieval_completed = next(
        index
        for index, (step, _, status) in enumerate(progress_events)
        if step.startswith("retrieval:") and status == "completed"
    )
    city_plan_started = next(
        index
        for index, (step, _, status) in enumerate(progress_events)
        if step.startswith("city_plan:") and status == "running"
    )
    details_started = next(
        index
        for index, (step, _, status) in enumerate(progress_events)
        if step.startswith("attraction_details:") and status == "running"
    )
    details_completed = next(
        index
        for index, (step, _, status) in enumerate(progress_events)
        if step.startswith("attraction_details:") and status == "completed"
    )
    # 景点图片只在最终行程确定后补充；文字增强统一在最终复核节点处理。
    assert retrieval_completed < city_plan_started < details_started < details_completed


@pytest.mark.asyncio
async def test_workflow_graph_recovers_route_failure_with_same_candidate_pool(catalog_repository):
    class RecoveringCityService(CityService):
        route_enrichment_service = object()
        schedule_builder = ScheduleBuilder(meal_minutes=0, buffer_minutes=0)

        async def retrieve_candidates(self, *, city_stay, requirement, top_k):
            result = await self.plan(
                city_stay=city_stay,
                requirement=requirement,
                top_k=top_k,
            )
            return result.attractions

        async def plan_with_routes(self, **kwargs):
            raise CityPlanningError("测试路线校验失败")

        async def plan_from_candidates(self, *, city_stay, requirement, attractions):
            return await self.plan(
                city_stay=city_stay,
                requirement=requirement,
                top_k=20,
            )

    progress_events = []
    city_service = RecoveringCityService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=city_service,
        route_enrichment_service=RouteService(),
        weather_enrichment_service=WeatherService(),
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
        evaluator=PlanEvaluator(),
    )

    async def progress(step, label, status):
        progress_events.append((step, label, status))

    result = await workflow.run_requirement(
        RequirementParser().parse("杭州玩一天，喜欢自然"),
        progress=progress,
    )

    assert result.plan.itinerary[0].attractions
    assert any(step == "planning_recovery" for step, _, _ in progress_events)


@pytest.mark.asyncio
async def test_workflow_runs_city_retrieval_and_route_planning_with_concurrency_three(
    catalog_repository,
):
    class ParallelCityService:
        route_enrichment_service = object()
        schedule_builder = ScheduleBuilder(meal_minutes=0, buffer_minutes=0)

        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def retrieve_candidates(self, *, city_stay, requirement, top_k):
            del requirement, top_k
            return [
                HybridSearchResult(
                    poi_id=f"{city_stay.city}-{day}",
                    name=f"{city_stay.city}景点{day}",
                    city=city_stay.city,
                    category="景区",
                    longitude=100.0 + day / 100,
                    latitude=25.0 + day / 100,
                    description="测试景点资料",
                    preference_score=0.8,
                    final_rank=day,
                )
                for day in range(1, city_stay.days + 1)
            ]

        async def plan_with_routes(
            self,
            *,
            city_stay,
            requirement,
            city_adcode,
            attractions,
            narrate=False,
        ):
            del requirement, city_adcode, attractions, narrate
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            days = [
                DayPlan(
                    day=day,
                    attractions=[f"{city_stay.city}景点{day}"],
                    description="测试行程安排，节奏合理。",
                )
                for day in range(1, city_stay.days + 1)
            ]
            result = CityPlanningResult(
                city_stay=city_stay,
                attractions=[
                    HybridSearchResult(
                        poi_id=f"{city_stay.city}-{day}",
                        name=f"{city_stay.city}景点{day}",
                        city=city_stay.city,
                        category="景区",
                        longitude=100.0 + day / 100,
                        latitude=25.0 + day / 100,
                        description="测试景点资料",
                        final_rank=day,
                    )
                    for day in range(1, city_stay.days + 1)
                ],
                city_plan=CityPlan(city=city_stay.city, days=days),
            )
            return RouteAwareCityPlanningResult(
                city_result=result,
                routes_by_day=[[] for _ in days],
                decisions=[],
            )

    city_service = ParallelCityService()
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=Bootstrap(),
        city_planning_service=city_service,
        route_enrichment_service=RouteService(),
        weather_enrichment_service=WeatherService(),
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
        evaluator=PlanEvaluator(),
    )

    result = await workflow.run("云南玩七天，想去两三个城市，喜欢自然、古城和摄影")

    assert len(result.plan.city_stays) == 3
    assert city_service.max_active == 3
