"""Fixed acceptance cases for the route-aware planning workflow.

These cases intentionally use deterministic local doubles.  They verify the
ordering and contracts of the core flow without depending on an LLM, Amap or
the quality of the live attraction catalogue.
"""

from types import SimpleNamespace

import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.evaluator import PlanEvaluator
from backend.models.evaluation import EvaluationResult
from backend.models.retrieval import HybridSearchResult
from backend.models.route import RouteSegment
from backend.planner.city import CityPlanningError
from backend.planner.service import CityPlanningService
from backend.planner.workflow import TravelPlanningWorkflow
from backend.requirements import RequirementParser
from backend.schedule import ScheduleBuilder


class _Bootstrap:
    async def ensure(self, *, city, **kwargs):
        return SimpleNamespace(ready_count=3, partial=False)


class _Weather:
    async def enrich_day(self, day):
        return day


class _Narrator:
    async def plan(self, **kwargs):
        raise CityPlanningError("acceptance case uses deterministic descriptions")


class _ContextEvaluator:
    def __init__(self):
        self.tags_by_poi = None

    def evaluate_with_context(self, plan, requirement, *, interest_tags_by_poi):
        del plan, requirement
        self.tags_by_poi = interest_tags_by_poi
        return EvaluationResult(passed=True, issues=[])


class _RouteService:
    def __init__(self, *, unavailable=False):
        self.unavailable = unavailable
        self.resolve_calls: list[tuple[str, str]] = []
        self.enrich_calls = 0

    async def resolve_segment(self, origin, destination, **kwargs):
        self.resolve_calls.append((origin.name, destination.name))
        if self.unavailable:
            return RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                mode="walking",
                source="unavailable",
                duration_min=None,
            )
        return RouteSegment(
            origin_poi_id=origin.poi_id,
            origin_name=origin.name,
            destination_poi_id=destination.poi_id,
            destination_name=destination.name,
            mode="walking",
            source="amap_mcp",
            distance_m=900,
            duration_min=15,
        )

    async def enrich_day(self, day, *, requested_mode):
        self.enrich_calls += 1
        raise AssertionError("route-aware workflow must not enrich the same day twice")


class _Retrieval:
    async def search(self, *, city, interests, preference_text, top_k):
        del interests, preference_text, top_k
        # Six candidates are enough for a three-day case while keeping the
        # acceptance data small and reproducible.
        return [
            HybridSearchResult(
                poi_id=f"{city}-poi-{index}",
                name=f"{city}景点{index}",
                city=city,
                category="景区",
                longitude=120.0 + index / 1000,
                latitude=30.0 + index / 1000,
                description=f"{city}的测试景点 {index}。",
                visit_duration_minutes=90,
                preference_score=1 - index / 100,
                preference_similarity=1 - index / 100,
                keyword_score=1 - index / 100,
                tag_match_count=1,
                final_rank=index,
            )
            for index in range(1, 7)
        ]


def _workflow(catalog_repository, route_service, evaluator=None):
    route_aware_service = CityPlanningService(
        retrieval_service=_Retrieval(),
        planner=_Narrator(),
        route_enrichment_service=route_service,
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
    )
    return TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=_Bootstrap(),
        city_planning_service=route_aware_service,
        route_enrichment_service=route_service,
        weather_enrichment_service=_Weather(),
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
        evaluator=evaluator or PlanEvaluator(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_city_count", "expected_days"),
    [
        ("杭州玩一天，喜欢自然，明天出发，每天08:00到18:00", 1, 1),
        ("成都玩三天，喜欢历史文化，明天出发，每天08:00到18:00", 1, 3),
        ("云南玩七天，喜欢自然，明天出发，每天08:00到18:00", 3, 7),
    ],
)
async def test_fixed_route_aware_acceptance_cases(
    catalog_repository,
    text,
    expected_city_count,
    expected_days,
):
    route_service = _RouteService()
    result = await _workflow(catalog_repository, route_service).run(text)

    assert len(result.plan.city_stays) == expected_city_count
    assert sum(item.days for item in result.plan.city_stays) == expected_days
    assert len(result.plan.itinerary) == expected_days
    assert all(day.schedule is not None for day in result.plan.itinerary)
    assert route_service.enrich_calls == 0
    assert all(
        route.source == "amap_mcp"
        for day in result.plan.itinerary
        for route in day.routes
    )


@pytest.mark.asyncio
async def test_short_window_and_unavailable_route_keep_flow_safe(catalog_repository):
    route_service = _RouteService(unavailable=True)
    workflow = _workflow(catalog_repository, route_service)

    # A route that cannot be resolved is never silently copied into the final
    # itinerary. The scheduler keeps the first feasible stop and the workflow
    # still produces a structurally valid one-day plan.
    result = await workflow.run("杭州玩一天，喜欢自然，明天出发，每天10:00到14:00")

    assert len(result.plan.itinerary) == 1
    assert len(result.plan.itinerary[0].attractions) == 1
    assert result.plan.itinerary[0].routes == []
    assert route_service.resolve_calls


@pytest.mark.asyncio
async def test_workflow_passes_retrieval_tags_to_evaluator_without_persisting_them(
    catalog_repository,
):
    route_service = _RouteService()
    evaluator = _ContextEvaluator()
    result = await _workflow(catalog_repository, route_service, evaluator).run(
        "杭州玩一天，喜欢自然，明天出发，每天08:00到18:00"
    )

    assert result.plan.evaluation is not None
    assert evaluator.tags_by_poi
    assert all(isinstance(tags, list) for tags in evaluator.tags_by_poi.values())
    assert all("interest_tags" not in stop.model_dump() for day in result.plan.itinerary for stop in day.attractions)
