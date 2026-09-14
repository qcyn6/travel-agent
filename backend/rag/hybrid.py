import logging

from backend.models.retrieval import AttractionDocument, HybridSearchResult
from backend.rag.bm25 import BM25Retriever, build_preference_query
from backend.rag.embedding import EmbeddingConfigurationError, EmbeddingError
from backend.rag.fusion import reciprocal_rank_fusion
from backend.rag.reranker import RerankingError

logger = logging.getLogger(__name__)


class HybridRetrievalService:
    def __init__(self, *, repository, vector_service, reranker, bm25=None):
        self.repository = repository
        self.vector_service = vector_service
        self.reranker = reranker
        self.bm25 = bm25 or BM25Retriever()

    async def search(
        self,
        *,
        city: str,
        interests: list[str],
        preference_text: str | None,
        top_k: int,
    ) -> list[HybridSearchResult]:
        documents: list[AttractionDocument] = self.repository.list_documents(city)
        if not documents:
            return []
        query = build_preference_query(interests, preference_text)
        if not query:
            query = "适合旅游的代表性景点"

        bm25_results = self.bm25.rank(documents, query, limit=10)
        bm25_ids = [poi_id for poi_id, _ in bm25_results]
        vector_results = []
        try:
            await self.vector_service.index_city(city)
            vector_results = await self.vector_service.search(city=city, query=query, limit=10)
        except (EmbeddingConfigurationError, EmbeddingError) as exc:
            logger.warning("向量检索不可用，降级为 BM25：%s", type(exc).__name__)

        # A zero-score BM25 query is still better served by deterministic
        # catalog order than by returning no candidates when vector search is
        # unavailable. Keep the actual BM25 ranks separate for observability.
        bm25_fusion_ids = bm25_ids or [document.poi_id for document in documents[:10]]
        vector_ids = [item.poi_id for item in vector_results]
        rankings = [bm25_fusion_ids]
        if vector_ids:
            rankings.append(vector_ids)
        fused = reciprocal_rank_fusion(rankings)
        document_by_id = {document.poi_id: document for document in documents}
        fused_documents = [document_by_id[poi_id] for poi_id, _ in fused if poi_id in document_by_id]

        try:
            ordered_ids = await self.reranker.rerank(query=query, candidates=fused_documents)
        except RerankingError as exc:
            logger.warning("DeepSeek 重排不可用，保留 RRF 顺序：%s", type(exc).__name__)
            ordered_ids = [document.poi_id for document in fused_documents]

        bm25_rank = {poi_id: rank for rank, poi_id in enumerate(bm25_ids, start=1)}
        vector_rank = {poi_id: rank for rank, poi_id in enumerate(vector_ids, start=1)}
        rrf_score = dict(fused)
        return [
            HybridSearchResult(
                **document_by_id[poi_id].model_dump(),
                bm25_rank=bm25_rank.get(poi_id),
                vector_rank=vector_rank.get(poi_id),
                rrf_score=rrf_score[poi_id],
                final_rank=rank,
            )
            for rank, poi_id in enumerate(ordered_ids[:top_k], start=1)
        ]
