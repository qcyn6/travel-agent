from fastapi import APIRouter, Depends, HTTPException, Query

from backend.attractions import AttractionRepository, get_attraction_repository
from backend.knowledge import KnowledgeRepository, get_knowledge_repository
from backend.models.attraction import AttractionKnowledgeState
from backend.models.knowledge import AttractionKnowledge


router = APIRouter(prefix="/api/v1/attractions", tags=["knowledge"])


@router.get("/{poi_id}/knowledge/status", response_model=AttractionKnowledgeState)
def get_attraction_knowledge_status(
    poi_id: str,
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
    knowledge_repository: KnowledgeRepository = Depends(get_knowledge_repository),
) -> AttractionKnowledgeState:
    attraction = attraction_repository.find_by_poi_id(poi_id)
    if attraction is None:
        raise HTTPException(status_code=404, detail="景点 POI 不存在，请先查询城市景点")
    knowledge = knowledge_repository.find(poi_id)
    return AttractionKnowledgeState(
        poi_id=attraction.poi_id,
        name=attraction.name,
        knowledge_status="ready" if knowledge else "pending",
        knowledge_error=None,
    )


@router.post("/{poi_id}/knowledge", response_model=AttractionKnowledge)
def enrich_attraction_knowledge(
    poi_id: str,
    force_refresh: bool = Query(default=False),
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
    knowledge_repository: KnowledgeRepository = Depends(get_knowledge_repository),
) -> AttractionKnowledge:
    del force_refresh
    attraction = attraction_repository.find_by_poi_id(poi_id)
    if attraction is None:
        raise HTTPException(status_code=404, detail="景点 POI 不存在，请先查询城市景点")
    knowledge = knowledge_repository.find(attraction.poi_id)
    if knowledge is None:
        raise HTTPException(
            status_code=409,
            detail="景点描述由本地数据库维护，当前 POI 尚未准备描述",
        )
    return knowledge
