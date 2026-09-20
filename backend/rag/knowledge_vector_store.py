"""Persistent Chroma index for province and city Markdown knowledge."""

from __future__ import annotations

import hashlib
from pathlib import Path

import chromadb

from backend.models.retrieval import KnowledgeSearchResult
from backend.rag.knowledge_source import KnowledgeChunk


class ChromaKnowledgeIndex:
    """Store ordinary-chat knowledge separately from attraction vectors."""

    def __init__(
        self,
        path: str,
        collection_name: str = "travel_knowledge",
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, documents: list[KnowledgeChunk], embeddings: list[list[float]]) -> int:
        if len(documents) != len(embeddings):
            raise ValueError("知识块数量与向量数量不一致")
        if not documents:
            return 0
        self.collection.upsert(
            ids=[document.doc_id for document in documents],
            documents=[document.index_text() for document in documents],
            embeddings=embeddings,
            metadatas=[_metadata_for_document(document) for document in documents],
        )
        return len(documents)

    def is_current(self, documents: list[KnowledgeChunk]) -> bool:
        if not documents:
            return True
        try:
            stored = self.collection.get(
                ids=[document.doc_id for document in documents],
                include=["metadatas"],
            )
        except Exception:
            return False

        metadata_by_id = {
            doc_id: metadata
            for doc_id, metadata in zip(
                stored.get("ids", []),
                stored.get("metadatas", []),
                strict=False,
            )
            if isinstance(metadata, dict)
        }
        return all(
            metadata_by_id.get(document.doc_id, {}).get("content_hash")
            == _content_hash(document)
            and metadata_by_id.get(document.doc_id, {}).get("entity_type")
            == document.entity_type
            for document in documents
        )

    def existing_ids(self) -> set[str]:
        return set(self.collection.get(include=[]).get("ids", []))

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def search(
        self,
        *,
        query_embedding: list[float],
        limit: int,
    ) -> list[KnowledgeSearchResult]:
        count = self.collection.count()
        if count == 0 or limit <= 0:
            return []
        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(limit, count),
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        results: list[KnowledgeSearchResult] = []
        for doc_id, indexed_text, metadata, distance in zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=True,
        ):
            if not isinstance(metadata, dict):
                continue
            results.append(
                KnowledgeSearchResult(
                    doc_id=doc_id,
                    entity_type=str(metadata.get("entity_type") or ""),
                    name=str(metadata.get("name") or ""),
                    province=str(metadata.get("province") or ""),
                    region_level=str(metadata.get("region_level") or ""),
                    source_file=str(metadata.get("source_file") or ""),
                    content=str(indexed_text or ""),
                    distance=max(0.0, float(distance)),
                )
            )
        return results


def _content_hash(document: KnowledgeChunk) -> str:
    return hashlib.sha256(document.index_text().encode("utf-8")).hexdigest()


def _metadata_for_document(document: KnowledgeChunk) -> dict[str, str]:
    return {
        "entity_type": document.entity_type,
        "name": document.name,
        "province": document.province,
        "region_level": document.region_level,
        "source_file": document.source_file,
        "content_hash": _content_hash(document),
    }


__all__ = ["ChromaKnowledgeIndex"]
