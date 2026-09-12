from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import Settings, get_settings
from backend.knowledge import KnowledgeRepository, get_knowledge_repository
from backend.models.retrieval import (
    HybridSearchRequest,
    HybridSearchResult,
    IndexBuildResult,
    VectorSearchResult,
)
from backend.rag import (
    EmbeddingConfigurationError,
    EmbeddingError,
    VectorRetrievalService,
)
from backend.rag.runtime import build_hybrid_service, build_vector_service


router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


def _service(repository: KnowledgeRepository, settings: Settings) -> VectorRetrievalService:
    return build_vector_service(repository, settings)


@router.post("/index", response_model=IndexBuildResult)
async def index_city_knowledge(
    city: str = Query(min_length=2),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    settings: Settings = Depends(get_settings),
) -> IndexBuildResult:
    try:
        count = await _service(repository, settings).index_city(city.strip())
        return IndexBuildResult(city=city.strip(), indexed_count=count)
    except EmbeddingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/vector", response_model=list[VectorSearchResult])
async def vector_search(
    city: str = Query(min_length=2),
    query: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    settings: Settings = Depends(get_settings),
) -> list[VectorSearchResult]:
    try:
        return await _service(repository, settings).search(
            city=city.strip(), query=query, limit=limit
        )
    except EmbeddingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/hybrid", response_model=list[HybridSearchResult])
async def hybrid_search(
    request: HybridSearchRequest,
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    settings: Settings = Depends(get_settings),
) -> list[HybridSearchResult]:
    service = build_hybrid_service(repository, settings)
    try:
        return await service.search(
            city=request.city.strip(),
            interests=request.interests,
            preference_text=request.preference_text,
            top_k=request.top_k,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
