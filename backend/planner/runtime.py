from backend.attractions import AttractionRepository
from backend.destination import DestinationPlanner, DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.knowledge import KnowledgeRepository
from backend.knowledge.runtime import build_bootstrap_service
from backend.planner.city import DeepSeekCityPlanner
from backend.planner.service import CityPlanningService
from backend.planner.workflow import TravelPlanningWorkflow
from backend.rag.runtime import build_hybrid_service
from backend.requirements.runtime import build_requirement_parser


def build_travel_workflow(session, settings) -> TravelPlanningWorkflow:
    catalog = CatalogRepository(session)
    attractions = AttractionRepository(session)
    knowledge = KnowledgeRepository(session)
    return TravelPlanningWorkflow(
        parser=build_requirement_parser(settings),
        resolver=DestinationResolver(catalog),
        destination_planner=DestinationPlanner(catalog),
        catalog_repository=catalog,
        knowledge_bootstrap=build_bootstrap_service(
            attraction_repository=attractions,
            knowledge_repository=knowledge,
            settings=settings,
        ),
        city_planning_service=CityPlanningService(
            retrieval_service=build_hybrid_service(knowledge, settings),
            planner=DeepSeekCityPlanner(
                settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
            ),
        ),
    )
