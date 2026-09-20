from backend.attractions import AttractionRepository
from backend.cache import build_cache
from backend.destination import DestinationPlanner, DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.evaluator import PlanEvaluator
from backend.knowledge import KnowledgeRepository
from backend.knowledge.runtime import build_bootstrap_service
from backend.poi.runtime import build_candidate_enrichment_service
from backend.planner.city import DeepSeekCityPlanner
from backend.planner.service import CityPlanningService
from backend.planner.workflow import TravelPlanningWorkflow
from backend.rag.runtime import build_hybrid_service
from backend.requirements.runtime import build_requirement_parser
from backend.route import (
    AmapMcpRouteClient,
    RouteConfigurationError,
    RouteEnrichmentService,
)
from backend.schedule import ScheduleBuilder
from backend.weather import QWeatherClient, WeatherEnrichmentService


def build_travel_workflow(session, settings) -> TravelPlanningWorkflow:
    catalog = CatalogRepository(session)
    attractions = AttractionRepository(session)
    knowledge = KnowledgeRepository(session)
    cache = build_cache(settings)
    attraction_enrichment_service = build_candidate_enrichment_service(
        attraction_repository=attractions,
        catalog_repository=catalog,
        settings=settings,
        cache=cache,
    )
    try:
        route_client = AmapMcpRouteClient(
            settings.amap_api_key,
            endpoint=getattr(settings, "amap_mcp_url", "https://mcp.amap.com/mcp"),
            timeout_seconds=getattr(settings, "amap_mcp_timeout_seconds", 15.0),
            sse_read_timeout_seconds=getattr(settings, "amap_mcp_sse_read_timeout_seconds", 20.0),
            cache=cache,
            cache_ttl_seconds=settings.redis_route_ttl_seconds,
        )
    except RouteConfigurationError:
        route_client = None

    qweather_client = QWeatherClient(
        settings.qweather_api_key,
        api_host=settings.qweather_api_host,
        timeout_seconds=settings.qweather_timeout_seconds,
        cache=cache,
        cache_ttl_seconds=settings.redis_weather_ttl_seconds,
    )
    return TravelPlanningWorkflow(
        parser=build_requirement_parser(settings),
        resolver=DestinationResolver(catalog),
        destination_planner=DestinationPlanner(catalog),
        catalog_repository=catalog,
        knowledge_bootstrap=build_bootstrap_service(
            attraction_repository=attractions,
            catalog_repository=catalog,
            knowledge_repository=knowledge,
            settings=settings,
            cache=cache,
        ),
        attraction_enrichment_service=attraction_enrichment_service,
        city_planning_service=CityPlanningService(
            retrieval_service=build_hybrid_service(knowledge, settings),
            planner=DeepSeekCityPlanner(
                settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
            ),
        ),
        route_enrichment_service=RouteEnrichmentService(
            route_client,
            max_concurrency=settings.route_enrichment_concurrency,
        ),
        weather_enrichment_service=WeatherEnrichmentService(
            qweather_client,
            max_concurrency=settings.weather_enrichment_concurrency,
        ),
        schedule_builder=ScheduleBuilder(
            day_start_time=settings.schedule_day_start_time,
            meal_minutes=settings.schedule_meal_minutes,
            buffer_minutes=settings.schedule_buffer_minutes,
        ),
        evaluator=PlanEvaluator(
            max_day_minutes=settings.evaluator_max_day_minutes,
            max_route_segment_minutes=settings.evaluator_max_route_segment_minutes,
            max_daily_route_minutes=settings.evaluator_max_daily_route_minutes,
            minimum_interest_coverage=settings.evaluator_minimum_interest_coverage,
        ),
    )
