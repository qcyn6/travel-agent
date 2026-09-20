from backend.attractions.dedupe import dedupe_attractions
from backend.models.retrieval import AttractionDocument, VectorSearchResult


class VectorRetrievalService:
    def __init__(self, *, repository, embedding_client, vector_index):
        self.repository = repository
        self.embedding_client = embedding_client
        self.vector_index = vector_index

    async def index_city(self, city: str) -> int:
        documents: list[AttractionDocument] = dedupe_attractions(
            self.repository.list_documents(city)
        )
        if not documents:
            return 0
        is_current = getattr(self.vector_index, "is_current", None)
        if callable(is_current) and is_current(documents):
            return 0
        embeddings = await self.embedding_client.embed(
            [document.index_text() for document in documents]
        )
        return self.vector_index.upsert(documents, embeddings)

    async def search(self, *, city: str, query: str, limit: int = 10) -> list[VectorSearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("检索 Query 不能为空")
        query_embedding = (await self.embedding_client.embed([normalized_query]))[0]
        results = dedupe_attractions(
            self.vector_index.search(
                city=city,
                query_embedding=query_embedding,
                limit=limit,
            )
        )
        # Chroma is a derived index.  Enforce the canonical database's active
        # set here too, so a row excluded by curation cannot leak through a
        # stale vector until the next index maintenance run.
        list_documents = getattr(self.repository, "list_documents", None)
        if not callable(list_documents):
            return results
        active_ids = {document.poi_id for document in list_documents(city)}
        return [result for result in results if result.poi_id in active_ids]
