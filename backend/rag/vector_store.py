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
                    "description": document.description,
                    "source_url": document.source_url or "",
                }
                for document in documents
            ],
        )
        return len(documents)

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
                description=metadata["description"],
                source_url=metadata["source_url"] or None,
                distance=max(0.0, float(distance)),
            )
            for poi_id, metadata, distance in zip(ids, metadatas, distances, strict=True)
        ]
