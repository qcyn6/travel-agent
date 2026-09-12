from fastapi import APIRouter, Depends, HTTPException, Query

from backend.attractions import AttractionRepository, get_attraction_repository
from backend.config import Settings, get_settings
from backend.knowledge import (
    KnowledgeRepository,
    SourceNotFoundError,
    SourceSearchError,
    SummarizationError,
    WebContentError,
    get_knowledge_repository,
)
from backend.knowledge.runtime import build_enrichment_service
from backend.models.attraction import AttractionKnowledgeState
from backend.models.knowledge import AttractionKnowledge


router = APIRouter(prefix="/api/v1/attractions", tags=["knowledge"])


@router.get("/{poi_id}/knowledge/status", response_model=AttractionKnowledgeState)
def get_attraction_knowledge_status(
    poi_id: str,
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
) -> AttractionKnowledgeState:
    attraction = attraction_repository.find_by_poi_id(poi_id)
    if attraction is None:
        raise HTTPException(status_code=404, detail="景点 POI 不存在，请先查询城市景点")
    return AttractionKnowledgeState.model_validate(attraction)


@router.post("/{poi_id}/knowledge", response_model=AttractionKnowledge)
async def enrich_attraction_knowledge(
    poi_id: str,
    force_refresh: bool = Query(default=False),
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
    knowledge_repository: KnowledgeRepository = Depends(get_knowledge_repository),
    settings: Settings = Depends(get_settings),
) -> AttractionKnowledge:
    attraction = attraction_repository.find_by_poi_id(poi_id)
    if attraction is None:
        raise HTTPException(status_code=404, detail="景点 POI 不存在，请先查询城市景点")

    try:
        service = build_enrichment_service(
            attraction_repository=attraction_repository,
            knowledge_repository=knowledge_repository,
            settings=settings,
        )
        return await service.enrich(
            poi_id=attraction.poi_id,
            name=attraction.name,
            city=attraction.city.name,
            category=attraction.category,
            force_refresh=force_refresh,
        )
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (SourceSearchError, WebContentError, SummarizationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
