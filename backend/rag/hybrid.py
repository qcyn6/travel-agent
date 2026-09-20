import logging

from backend.attractions.dedupe import dedupe_attractions
from backend.models.retrieval import AttractionDocument, HybridSearchResult
from backend.rag.bm25 import BM25Retriever, build_preference_query
from backend.rag.embedding import EmbeddingConfigurationError, EmbeddingError
from backend.tag_matching import tag_match_score

logger = logging.getLogger(__name__)


class HybridRetrievalService:
    """Retrieve attractions with explicit tags plus preference semantics.

    Interest selection is deliberately deterministic: normalized interests are
    hard-matched against ``attractions.tags``. The vector index is used only
    for the user's free-form travel preference, so description semantics do
    not silently override an explicit interest choice.

    ``reranker`` remains accepted as an optional constructor argument for
    integrations written against the previous API, but it is intentionally
    unused. The optional BM25 instance provides the keyword half of the new
    preference search.
    """

    def __init__(self, *, repository, vector_service, reranker=None, bm25=None):
        del reranker
        self.repository = repository
        self.vector_service = vector_service
        self.bm25 = bm25 or BM25Retriever()

    async def search(
        self,
        *,
        city: str,
        interests: list[str],
        preference_text: str | None,
        top_k: int,
    ) -> list[HybridSearchResult]:
        try:
            documents: list[AttractionDocument] = self.repository.list_documents(
                city,
                interests=interests,
            )
        except TypeError as exc:
            # Keep lightweight repository doubles and older integrations
            # working while the database repository adopts tag-aware ordering.
            if "unexpected keyword argument" not in str(exc):
                raise
            documents = self.repository.list_documents(city)

        documents = dedupe_attractions(documents)
        if not documents:
            return []

        # Interests are a hard database-tag filter. In particular, a word in
        # a description or attraction name is not enough to qualify an item.
        tag_scores = {
            document.poi_id: tag_match_score(document.interest_tags, interests)
            for document in documents
        }
        if interests:
            documents = [
                document
                for document in documents
                if tag_scores[document.poi_id] > 0
            ]
            if not documents:
                return []

        # Only free-form preference text is sent to semantic/keyword search.
        # The existing rewrite/expansion helper is retained, but interests
        # are intentionally not passed into it.
        preference_query = build_preference_query([], preference_text)
        vector_results = []
        keyword_results = []
        if preference_query:
            keyword_results = self.bm25.rank(
                documents,
                preference_query,
                limit=len(documents),
                text_getter=lambda document: document.description,
            )
            try:
                await self.vector_service.index_city(city)
                vector_results = await self.vector_service.search(
                    city=city,
                    query=preference_query,
                    limit=min(len(documents), max(top_k * 5, top_k)),
                )
            except (EmbeddingConfigurationError, EmbeddingError) as exc:
                logger.warning("偏好向量检索不可用，保留标签匹配顺序：%s", type(exc).__name__)

        document_by_id = {document.poi_id: document for document in documents}
        vector_rank = {
            item.poi_id: rank
            for rank, item in enumerate(vector_results, start=1)
            if item.poi_id in document_by_id
        }
        preference_similarity = {
            item.poi_id: max(0.0, 1.0 - float(item.distance))
            for item in vector_results
            if item.poi_id in document_by_id
        }
        raw_keyword_scores = {
            poi_id: max(0.0, float(score))
            for poi_id, score in keyword_results
            if poi_id in document_by_id
        }
        max_keyword_score = max(raw_keyword_scores.values(), default=0.0)
        keyword_scores = {
            poi_id: score / max_keyword_score
            for poi_id, score in raw_keyword_scores.items()
        }
        catalog_position = {
            document.poi_id: position
            for position, document in enumerate(documents)
        }
        preference_scores = {
            document.poi_id: (
                0.7 * preference_similarity.get(document.poi_id, 0.0)
                + 0.3 * keyword_scores.get(document.poi_id, 0.0)
            )
            for document in documents
        }
        ordered_documents = sorted(
            documents,
            key=lambda document: (
                -preference_scores[document.poi_id],
                -tag_scores[document.poi_id],
                catalog_position[document.poi_id],
            ),
        )
        tag_rank = {
            document.poi_id: rank
            for rank, document in enumerate(ordered_documents, start=1)
        }

        return [
            HybridSearchResult(
                **document.model_dump(),
                tag_match_count=tag_scores[document.poi_id],
                preference_similarity=preference_similarity.get(document.poi_id),
                keyword_score=keyword_scores.get(document.poi_id, 0.0),
                preference_score=preference_scores[document.poi_id],
                # Legacy fields are retained for response compatibility. No
                # RRF or AI reranker score is produced by the new path.
                bm25_rank=None,
                interest_rank=tag_rank[document.poi_id],
                vector_rank=vector_rank.get(document.poi_id),
                rrf_score=0,
                final_rank=rank,
            )
            for rank, document in enumerate(ordered_documents[:top_k], start=1)
        ]
