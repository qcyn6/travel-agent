from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.destination import DestinationPlanner, DestinationResolver
from backend.evaluator import PlanEvaluator
from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult
from backend.models.route import RouteSegment
from backend.models.weather import WeatherResult
from backend.planner.workflow import TravelPlanningWorkflow
from backend.requirements import RequirementParser
from backend.schedule import ScheduleBuilder


GOLDEN_ATTRACTIONS = [
    ("DALI-01", "金梭岛"),
    ("DALI-02", "海东风景区"),
    ("DALI-03", "洱海月湿地公园"),
    ("DALI-04", "大理苍山中和索道"),
    ("DALI-05", "苍山景区苍山感通索道"),
    ("DALI-06", "清碧溪"),
]


class GoldenBootstrap:
    async def ensure(self, *, city, **kwargs):
        return SimpleNamespace(city=city.name, ready_count=len(GOLDEN_ATTRACTIONS), partial=False)


class GoldenCityService:
    async def plan(self, *, city_stay, requirement, top_k):
        attractions = [
            HybridSearchResult(
                poi_id=poi_id,
                name=name,
                city=city_stay.city,
                category="自然山水",
                interest_tags=["自然", "摄影"],
                longitude=100.1 + index / 100,
                latitude=25.6 + index / 100,
                description=f"{name}适合自然风景和摄影体验。",
                rrf_score=1 / index,
                final_rank=index,
            )
            for index, (poi_id, name) in enumerate(GOLDEN_ATTRACTIONS, start=1)
        ]
        # Deliberately reproduce the kind of output shown in the screenshot.
        # The workflow boundary must repair it before the plan is returned.
        city_plan = CityPlan(
            city=city_stay.city,
            days=[
                DayPlan(
                    day=1,
                    attractions=["金梭岛", "海东风景区", "洱海月湿地公园"],
                    description="第一天沿洱海体验自然风景和摄影路线。",
                ),
                DayPlan(
                    day=2,
                    attractions=["大理苍山中和索道", "苍山景区苍山感通索道", "洱海月湿地公园"],
                    description="第二天安排苍山自然景观和山地体验。",
                ),
                DayPlan(
                    day=3,
                    attractions=["苍山景区苍山感通索道", "清碧溪", "洱海月湿地公园"],
                    description="第三天安排苍山溪谷和自然风景体验。",
                ),
            ],
        )
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=attractions,
            city_plan=city_plan,
        )


class GoldenRouteService:
    async def enrich_day(self, day, *, requested_mode):
        routes = [
            RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                origin_longitude=origin.longitude,
                origin_latitude=origin.latitude,
                destination_longitude=destination.longitude,
                destination_latitude=destination.latitude,
                distance_m=1500,
                duration_min=15,
                mode="transit",
                source="amap_mcp",
            )
            for origin, destination in zip(day.attractions, day.attractions[1:])
        ]
        return day.model_copy(update={"routes": routes})


class GoldenWeatherService:
    async def enrich_day(self, day):
        return day.model_copy(update={
            "weather": WeatherResult(
                city=day.city,
                date=day.date,
                condition="晴",
                temperature_min=16,
                temperature_max=25,
                precipitation_probability=0.1,
                wind_level="2级",
                status="available",
                source="qweather",
            )
        })


@pytest.mark.asyncio
async def test_dali_golden_case_is_unique_complete_and_passes_review(catalog_repository):
    workflow = TravelPlanningWorkflow(
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        destination_planner=DestinationPlanner(catalog_repository),
        catalog_repository=catalog_repository,
        knowledge_bootstrap=GoldenBootstrap(),
        city_planning_service=GoldenCityService(),
        route_enrichment_service=GoldenRouteService(),
        weather_enrichment_service=GoldenWeatherService(),
        schedule_builder=ScheduleBuilder(),
        evaluator=PlanEvaluator(),
    )
    requirement = TravelRequirement(
        province="云南省",
        cities=["大理市"],
        days=3,
        interests=["自然", "摄影"],
        preference_text="行程轻松",
        travel_scope="single_city",
        start_date=date.today() + timedelta(days=7),
        transport_mode="transit",
    )

    result = await workflow.run_requirement(requirement)
    plan = result.plan
    poi_ids = [stop.poi_id for day in plan.itinerary for stop in day.attractions]

    assert len(plan.itinerary) == 3
    assert all(day.attractions for day in plan.itinerary)
    assert len(poi_ids) == len(set(poi_ids))
    assert all(route.source == "amap_mcp" for day in plan.itinerary for route in day.routes)
    assert all(day.schedule is not None and day.schedule.status == "complete" for day in plan.itinerary)
    assert plan.evaluation is not None
    assert plan.evaluation.passed is True
    assert plan.evaluation.issues == []
    assert plan.notices == []
