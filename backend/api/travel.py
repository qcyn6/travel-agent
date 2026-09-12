from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.attractions import AttractionRepository
from backend.config import Settings, get_settings
from backend.database import get_db
from backend.destination import DestinationPlanner, DestinationPlanningError, DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.knowledge import KnowledgeBootstrapError, KnowledgeRepository
from backend.knowledge.runtime import build_bootstrap_service
from backend.models.travel_plan import (
    DestinationChoiceResponse,
    TravelPlanRequest,
    TravelPlanResponse,
)
from backend.planner import (
    CityPlanningService,
    DeepSeekCityPlanner,
    DestinationChoiceRequired,
    TravelPlanningWorkflow,
)
from backend.rag.runtime import build_hybrid_service
from backend.requirements.runtime import build_requirement_parser


router = APIRouter(prefix="/api/v1/travel", tags=["travel"])


@router.post(
    "/plan",
    response_model=TravelPlanResponse | DestinationChoiceResponse,
)
async def create_travel_plan(
    request: TravelPlanRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TravelPlanResponse | DestinationChoiceResponse:
    catalog = CatalogRepository(session)
    attractions = AttractionRepository(session)
    knowledge = KnowledgeRepository(session)
    workflow = TravelPlanningWorkflow(
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
    try:
        return await workflow.run(request.text, travel_scope=request.travel_scope)
    except DestinationChoiceRequired as exc:
        return DestinationChoiceResponse(
            requirement=exc.requirement,
            destination_options=exc.options,
        )
    except (ValueError, DestinationPlanningError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KnowledgeBootstrapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="旅游方案生成失败") from exc
