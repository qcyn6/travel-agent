from fastapi import APIRouter, Depends, HTTPException

from backend.config import Settings, get_settings
from backend.destination import DestinationResolutionError, DestinationResolver
from backend.destination.repository import CatalogRepository, get_catalog_repository
from backend.knowledge import KnowledgeRepository, get_knowledge_repository
from backend.models.itinerary import CityPlanningRequest, CityPlanningResult
from backend.models.requirement import MAX_SINGLE_CITY_DAYS
from backend.planner import CityPlanningError, CityPlanningService, DeepSeekCityPlanner
from backend.rag import EmbeddingError
from backend.rag.runtime import build_hybrid_service
from backend.security import reject_legacy_planning


router = APIRouter(prefix="/api/v1/planning", tags=["planning"])


@router.post("/city", response_model=CityPlanningResult, dependencies=[Depends(reject_legacy_planning)])
async def plan_city(
    request: CityPlanningRequest,
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    catalog_repository: CatalogRepository = Depends(get_catalog_repository),
    settings: Settings = Depends(get_settings),
) -> CityPlanningResult:
    try:
        normalized_requirement = DestinationResolver(catalog_repository).resolve(
            request.requirement
        ).requirement
    except DestinationResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.city_stay.days > MAX_SINGLE_CITY_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"单城市规划目前只支持1～{MAX_SINGLE_CITY_DAYS}天；超过3天请改为同省多城市",
        )
    if request.city_stay.days != normalized_requirement.days:
        raise HTTPException(status_code=422, detail="单城市规划的 CityStay 天数必须等于总天数")
    if normalized_requirement.cities and request.city_stay.city not in normalized_requirement.cities:
        raise HTTPException(status_code=422, detail="CityStay 城市不在用户需求城市中")
    service = CityPlanningService(
        retrieval_service=build_hybrid_service(repository, settings),
        planner=DeepSeekCityPlanner(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
    try:
        return await service.plan(
            city_stay=request.city_stay,
            requirement=normalized_requirement,
            top_k=request.top_k,
        )
    except (EmbeddingError, CityPlanningError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
