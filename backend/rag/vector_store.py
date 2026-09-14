import hashlib
from pathlib import Path

import chromadb

from backend.models.retrieval import AttractionDocument, VectorSearchResult


class ChromaAttractionIndex:
    def __init__(self, path: str, collection_name: str = "attraction_knowledge"):
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, documents: list[AttractionDocument], embeddings: list[list[float]]) -> int:
        if len(documents) != len(embeddings):
            raise ValueError("文档数量与向量数量不一致")
        if not documents:
            return 0
        self.collection.upsert(
            ids=[document.poi_id for document in documents],
            documents=[document.index_text() for document in documents],
            embeddings=embeddings,
            metadatas=[
                {
                    "name": document.name,
                    "city": document.city,
                    "category": document.category or "",
                    "longitude": document.longitude if document.longitude is not None else "",
                    "latitude": document.latitude if document.latitude is not None else "",
                    "description": document.description,
                    "source_url": document.source_url or "",
                    "content_hash": _content_hash(document),
                }
                for document in documents
            ],
        )
        return len(documents)

    def is_current(self, documents: list[AttractionDocument]) -> bool:
        """Return whether the persistent index already contains these documents."""
        if not documents:
            return True
        try:
            stored = self.collection.get(
                ids=[document.poi_id for document in documents],
                include=["metadatas"],
            )
        except Exception:
            # A failed consistency check should rebuild the index rather than make
            # retrieval unavailable because of a stale or partially-created index.
            return False

        stored_ids = stored.get("ids", [])
        stored_metadatas = stored.get("metadatas", [])
        metadata_by_id = {
            poi_id: metadata
            for poi_id, metadata in zip(stored_ids, stored_metadatas)
            if isinstance(metadata, dict)
        }
        return all(
            metadata_by_id.get(document.poi_id, {}).get("content_hash")
            == _content_hash(document)
            for document in documents
        )

    def search(self, *, city: str, query_embedding: list[float], limit: int) -> list[VectorSearchResult]:
        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where={"city": city},
            include=["metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            VectorSearchResult(
                poi_id=poi_id,
                name=metadata["name"],
                city=metadata["city"],
                category=metadata["category"] or None,
                longitude=float(metadata["longitude"]) if metadata.get("longitude") != "" else None,
                latitude=float(metadata["latitude"]) if metadata.get("latitude") != "" else None,
                description=metadata["description"],
                source_url=metadata["source_url"] or None,
                distance=max(0.0, float(distance)),
            )
            for poi_id, metadata, distance in zip(ids, metadatas, distances, strict=True)
        ]


def _content_hash(document: AttractionDocument) -> str:
    return hashlib.sha256(document.index_text().encode("utf-8")).hexdigest()
