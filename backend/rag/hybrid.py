import asyncio
import hashlib
import json
import logging
from collections import OrderedDict

from backend.attractions.dedupe import dedupe_attractions
from backend.models.retrieval import AttractionDocument, HybridSearchResult
from backend.rag.bm25 import BM25Retriever
from backend.rag.embedding import EmbeddingConfigurationError, EmbeddingError
from backend.rag.preference_rewriter import PreferenceQueryRewrite
from backend.tag_matching import compact_tag_match_score

logger = logging.getLogger(__name__)

_NO_EXTRA_PREFERENCE_VALUES = frozenset(
    {"没有额外体验偏好", "没有额外偏好", "无额外偏好", "无特殊偏好"}
)


class HybridRetrievalService:
    """Retrieve attractions with explicit tags plus preference semantics.

    Interest selection is deliberately deterministic: normalized interests are
    hard-matched against ``attractions.tags`` and the bounded tag shortlist is
    formed first. The vector index is then used only for the user's free-form
    travel preference inside that shortlist, so description semantics do not
    silently override an explicit interest choice.

    The optional BM25 instance provides the keyword half of preference
    search. Experimental rerankers are isolated under ``experiments``.
    """

    def __init__(
        self,
        *,
        repository,
        vector_service,
        bm25=None,
        preference_rewriter=None,
    ):
        self.repository = repository
        self.vector_service = vector_service
        self.bm25 = bm25 or BM25Retriever()
        self.preference_rewriter = preference_rewriter
        self._preference_rewrite_cache: OrderedDict[
            str, tuple[str, str]
        ] = OrderedDict()
        self._preference_rewrite_lock = asyncio.Lock()
        self._preference_rewrite_tasks: dict[
            str, asyncio.Task[tuple[str, str]]
        ] = {}

    async def search(
        self,
        *,
        city: str,
        interests: list[str],
        preference_text: str | None,
        top_k: int | None,
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
            document.poi_id: compact_tag_match_score(document.interest_tags, interests)
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

        # Bound the semantic/keyword stage to the tag-qualified shortlist.
        # The workflow passes top_k=20, so preference retrieval can reorder
        # only those first 20 tag matches instead of allowing a weakly tagged
        # item outside the shortlist to displace them.
        catalog_position = {
            document.poi_id: position
            for position, document in enumerate(documents)
        }
        tag_ordered_documents = sorted(
            documents,
            key=lambda document: (
                -tag_scores[document.poi_id],
                catalog_position[document.poi_id],
            ),
        )
        if top_k is not None:
            documents = tag_ordered_documents[:top_k]
        else:
            documents = tag_ordered_documents
        catalog_position = {
            document.poi_id: position
            for position, document in enumerate(documents)
        }

        # Only free-form preference text is rewritten, after the interest
        # shortlist is fixed. The model sees descriptions from that shortlist
        # but cannot add candidates or turn preferences into hard constraints.
        normalized_preference = (preference_text or "").strip()
        semantic_query, preference_query = ("", "")
        if (
            normalized_preference
            and normalized_preference not in _NO_EXTRA_PREFERENCE_VALUES
        ):
            semantic_query, preference_query = await self._preference_queries(
                normalized_preference,
                documents,
            )
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
                    query=semantic_query,
                    limit=(
                        len(documents)
                        if top_k is None
                        else min(len(documents), max(top_k * 5, top_k))
                    ),
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

        selected_documents = (
            ordered_documents if top_k is None else ordered_documents[:top_k]
        )
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
            for rank, document in enumerate(selected_documents, start=1)
        ]

    async def _preference_queries(
        self,
        preference_text: str,
        documents: list[AttractionDocument],
    ) -> tuple[str, str]:
        """Rewrite against the active shortlist, then reuse for both retrievers."""

        if self.preference_rewriter is None:
            return preference_text, preference_text

        candidate_descriptions = [
            {
                "name": document.name,
                "category": document.category,
                "description": document.description,
            }
            for document in documents[:20]
            if document.description.strip()
        ]
        if not candidate_descriptions:
            return preference_text, preference_text

        cache_material = json.dumps(
            {
                "preference": " ".join(preference_text.casefold().split()),
                "candidates": candidate_descriptions,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
        cached = self._preference_rewrite_cache.get(cache_key)
        if cached is not None:
            self._preference_rewrite_cache.move_to_end(cache_key)
            return cached

        # City planning can retrieve multiple cities concurrently. Only the
        # same preference + same shortlist share an in-flight rewrite; distinct
        # cities do not block one another.
        async with self._preference_rewrite_lock:
            cached = self._preference_rewrite_cache.get(cache_key)
            if cached is not None:
                self._preference_rewrite_cache.move_to_end(cache_key)
                return cached
            task = self._preference_rewrite_tasks.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._rewrite_preference(
                        preference_text,
                        candidate_descriptions,
                    )
                )
                self._preference_rewrite_tasks[cache_key] = task

        try:
            result = await asyncio.shield(task)
        finally:
            if task.done() and self._preference_rewrite_tasks.get(cache_key) is task:
                async with self._preference_rewrite_lock:
                    if self._preference_rewrite_tasks.get(cache_key) is task:
                        self._preference_rewrite_tasks.pop(cache_key, None)
                        if not task.cancelled() and task.exception() is None:
                            self._preference_rewrite_cache[cache_key] = task.result()
                            self._preference_rewrite_cache.move_to_end(cache_key)
                            while len(self._preference_rewrite_cache) > 64:
                                self._preference_rewrite_cache.popitem(last=False)
        return result

    async def _rewrite_preference(
        self,
        preference_text: str,
        candidate_descriptions: list[dict[str, str | None]],
    ) -> tuple[str, str]:
        try:
            rewritten: PreferenceQueryRewrite = await self.preference_rewriter.rewrite(
                preference_text,
                candidate_descriptions=candidate_descriptions,
            )
            semantic_query = rewritten.semantic_query.strip()
            keyword_query = " ".join(
                dict.fromkeys([semantic_query, *rewritten.keyword_terms])
            )
            return semantic_query, keyword_query
        except Exception as exc:
            logger.warning(
                "自由偏好语义改写不可用，回退到原始偏好：%s",
                type(exc).__name__,
            )
            return preference_text, preference_text
