"""Hybrid retrieval for the travel-topic knowledge used by ordinary chat."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Sequence
import re

from backend.models.retrieval import KnowledgeSearchResult
from backend.rag.bm25 import BM25Retriever
from backend.rag.embedding import EmbeddingConfigurationError, EmbeddingError
from backend.rag.fusion import reciprocal_rank_fusion
from backend.rag.knowledge_source import (
    KnowledgeChunk,
    TravelGuideKnowledgeSource,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdaptiveKnowledgeContext:
    """The bounded result of one ordinary-chat adaptive retrieval turn."""

    context: str | None
    query: str
    attempts: int
    sufficient: bool


class KnowledgeRetrievalService:
    """Combine local BM25 with a persistent semantic knowledge index."""

    def __init__(
        self,
        *,
        source: TravelGuideKnowledgeSource,
        embedding_client,
        vector_index,
        bm25: BM25Retriever | None = None,
        filter_destinations: bool = True,
    ):
        self.source = source
        self.embedding_client = embedding_client
        self.vector_index = vector_index
        self.bm25 = bm25 or BM25Retriever()
        self.filter_destinations = filter_destinations

    async def index_all(self) -> int:
        documents = self.source.list_documents()
        current = self.vector_index.is_current(documents)
        existing_ids = self.vector_index.existing_ids()
        desired_ids = {document.doc_id for document in documents}
        stale_ids = existing_ids - desired_ids

        if current:
            if stale_ids:
                self.vector_index.delete(list(stale_ids))
            return 0

        # Only remove stale rows after all embeddings succeed. A failed API
        # request must leave the previously usable index intact.
        embeddings = await self.embedding_client.embed(
            [document.index_text() for document in documents]
        )
        if stale_ids:
            self.vector_index.delete(list(stale_ids))
        return self.vector_index.upsert(documents, embeddings)

    async def search(
        self,
        query: str,
        *,
        limit: int = 4,
        destination_names: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchResult]:
        normalized_query = query.strip()
        if not normalized_query or limit <= 0:
            return []

        documents = self.source.list_documents()
        if self.filter_destinations:
            documents = _filter_destinations(documents, destination_names)
        if not documents:
            return []

        candidate_limit = max(limit * 3, 10)
        bm25_results = self.bm25.rank(
            documents,
            normalized_query,
            limit=candidate_limit,
        )
        bm25_ids = [doc_id for doc_id, _ in bm25_results]
        allowed_ids = {document.doc_id for document in documents}

        vector_results = []
        try:
            query_embedding = (await self.embedding_client.embed([normalized_query]))[0]
            vector_results = self.vector_index.search(
                query_embedding=query_embedding,
                limit=candidate_limit,
            )
            vector_results = [
                result for result in vector_results if result.doc_id in allowed_ids
            ]
        except (EmbeddingConfigurationError, EmbeddingError) as exc:
            logger.warning("普通对话语义检索不可用，降级为 BM25：%s", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - retrieval must not block ordinary chat
            logger.warning("普通对话知识库向量检索失败，降级为 BM25：%s", type(exc).__name__)

        vector_ids = [result.doc_id for result in vector_results]
        rankings = [bm25_ids]
        if vector_ids:
            rankings.append(vector_ids)
        fused = reciprocal_rank_fusion(rankings)
        document_by_id = {document.doc_id: document for document in documents}
        distance_by_id = {result.doc_id: result.distance for result in vector_results}
        return [
            KnowledgeSearchResult(
                doc_id=doc_id,
                entity_type=document.entity_type,
                name=document.name,
                province=document.province,
                region_level=document.region_level,
                source_file=document.source_file,
                content=document.content,
                distance=distance_by_id.get(doc_id, 0.0),
                retrieval_score=score,
            )
            for doc_id, score in fused[:limit]
            if (document := document_by_id.get(doc_id)) is not None
        ]

    async def context(
        self,
        query: str,
        *,
        limit: int = 4,
        destination_names: Sequence[str] | None = None,
        max_chars: int = 6000,
    ) -> str | None:
        results = await self.search(
            query,
            limit=limit,
            destination_names=destination_names,
        )
        if not results:
            return None
        return self.format_context(results, max_chars=max_chars)

    async def adaptive_context(
        self,
        query: str,
        *,
        limit: int = 4,
        destination_names: Sequence[str] | None = None,
        max_chars: int = 6000,
    ) -> AdaptiveKnowledgeContext:
        """Retrieve grounded context with at most one deterministic rewrite.

        The normal path is still one BM25 + Chroma search. A rewrite is only
        attempted when that search returns no usable chunks, which keeps
        ordinary chat fast while giving conversational questions a recovery
        path when filler words hide the travel topic.
        """

        current_query = query.strip()
        attempts = 0
        for _ in range(2):
            attempts += 1
            results = await self.search(
                current_query,
                limit=limit,
                destination_names=destination_names,
            )
            results = self._relevance_filter(results)
            if results:
                return AdaptiveKnowledgeContext(
                    context=self.format_context(results, max_chars=max_chars),
                    query=current_query,
                    attempts=attempts,
                    sufficient=True,
                )

            rewritten = self.rewrite_query(
                current_query,
                destination_names=destination_names,
            )
            if not rewritten or rewritten == current_query:
                break
            current_query = rewritten

        return AdaptiveKnowledgeContext(
            context=None,
            query=current_query,
            attempts=attempts,
            sufficient=False,
        )

    @staticmethod
    def rewrite_query(
        query: str,
        *,
        destination_names: Sequence[str] | None = None,
    ) -> str:
        """Strip conversational filler and add small travel synonym hints."""

        rewritten = re.sub(
            r"(?:请问|麻烦|帮我|帮忙|介绍一下|告诉我|我想了解|想了解|有没有|有哪些|有什么)",
            " ",
            query,
        )
        rewritten = re.sub(r"(?:吗|呢|啊|呀|吧)[？?。！!]*$", " ", rewritten)
        for source, synonyms in {
            "拍照": "摄影 取景",
            "慢慢逛": "漫步 慢游 休闲",
            "历史文化": "历史 人文 文化 古迹",
            "自然风景": "自然 山水 风景",
            "好吃": "美食 餐饮",
        }.items():
            if source in rewritten:
                rewritten = f"{rewritten} {synonyms}"
        destination_text = " ".join(
            str(name).strip()
            for name in (destination_names or [])
            if str(name).strip()
        )
        if destination_text and destination_text not in rewritten:
            rewritten = f"{destination_text} {rewritten}"
        return re.sub(r"\s+", " ", rewritten).strip()

    @staticmethod
    def _relevance_filter(results):
        """Remove empty and duplicate chunks before judging sufficiency."""

        seen: set[str] = set()
        filtered = []
        for result in results:
            if not result.content.strip() or result.doc_id in seen:
                continue
            seen.add(result.doc_id)
            filtered.append(result)
        return filtered

    @staticmethod
    def format_context(
        results: Sequence[KnowledgeSearchResult],
        *,
        max_chars: int = 6000,
    ) -> str | None:
        chunks: list[str] = []
        total = 0
        for result in results:
            label = {
                "province": "省份",
                "city": "城市",
                "guide": "主题",
            }.get(result.entity_type, "知识")
            chunk = f"【{label}知识：{result.name}】\n{result.content.strip()}"
            if total + len(chunk) > max_chars:
                remaining = max_chars - total
                if remaining > 80:
                    chunks.append(chunk[:remaining].rstrip() + "……")
                break
            chunks.append(chunk)
            total += len(chunk) + 2
        return "\n\n".join(chunks) if chunks else None


def _filter_destinations(
    documents: list[KnowledgeChunk],
    destination_names: Sequence[str] | None,
) -> list[KnowledgeChunk]:
    names = {str(name).strip() for name in (destination_names or []) if str(name).strip()}
    if not names:
        return documents
    return [
        document
        for document in documents
        if document.name in names or document.province in names
    ]


__all__ = ["AdaptiveKnowledgeContext", "KnowledgeRetrievalService"]
