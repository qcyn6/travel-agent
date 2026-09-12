from backend.models.retrieval import AttractionDocument, HybridSearchResult
from backend.rag.bm25 import BM25Retriever, build_preference_query
from backend.rag.fusion import reciprocal_rank_fusion
from backend.rag.reranker import RerankingError


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
        await self.vector_service.index_city(city)
        vector_results = await self.vector_service.search(city=city, query=query, limit=10)
        bm25_ids = [poi_id for poi_id, _ in bm25_results]
        vector_ids = [item.poi_id for item in vector_results]
        fused = reciprocal_rank_fusion([bm25_ids, vector_ids])
        document_by_id = {document.poi_id: document for document in documents}
        fused_documents = [document_by_id[poi_id] for poi_id, _ in fused if poi_id in document_by_id]

        try:
            ordered_ids = await self.reranker.rerank(query=query, candidates=fused_documents)
        except RerankingError:
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
