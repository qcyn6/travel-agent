import httpx
import pytest

from backend.models.retrieval import AttractionDocument
from backend.rag.embedding import (
    EmbeddingConfigurationError,
    EmbeddingError,
    OpenAICompatibleEmbeddingClient,
)
from backend.rag.service import VectorRetrievalService
from backend.rag.vector_store import ChromaAttractionIndex


def document(poi_id: str, name: str, city: str) -> AttractionDocument:
    return AttractionDocument(
        poi_id=poi_id,
        name=name,
        city=city,
        category="风景名胜",
        description=f"{name}适合自然风光、摄影和慢节奏游览。",
        source_url="https://example.gov.cn/source",
    )


@pytest.mark.asyncio
async def test_embedding_client_preserves_input_order():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vectors = await OpenAICompatibleEmbeddingClient(
            "embedding-key",
            base_url="https://embedding.test/v1",
            model="test-model",
            dimensions=3,
            client=client,
        ).embed(["西湖", "灵隐寺"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert requests[0].url.path == "/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer embedding-key"


@pytest.mark.asyncio
async def test_embedding_client_requires_its_own_key():
    with pytest.raises(EmbeddingConfigurationError, match="EMBEDDING_API_KEY"):
        await OpenAICompatibleEmbeddingClient(
            None,
            base_url="https://embedding.test/v1",
            model="test-model",
        ).embed(["摄影"])


@pytest.mark.asyncio
async def test_embedding_client_rejects_wrong_dimensions():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="维度"):
            await OpenAICompatibleEmbeddingClient(
                "embedding-key",
                base_url="https://embedding.test/v1",
                model="test-model",
                dimensions=3,
                client=client,
            ).embed(["摄影"])


def test_chroma_search_is_filtered_by_city(tmp_path):
    index = ChromaAttractionIndex(str(tmp_path / "chroma"), "test_attractions")
    documents = [
        document("HZ1", "西湖", "杭州市"),
        document("DL1", "洱海", "大理市"),
    ]
    index.upsert(documents, [[1.0, 0.0], [0.0, 1.0]])

    results = index.search(city="杭州市", query_embedding=[0.0, 1.0], limit=10)

    assert [result.poi_id for result in results] == ["HZ1"]
    assert results[0].city == "杭州市"


@pytest.mark.asyncio
async def test_vector_service_indexes_name_category_and_description():
    documents = [document("HZ1", "西湖", "杭州市")]

    class Repository:
        def list_documents(self, city):
            assert city == "杭州市"
            return documents

    class Embeddings:
        texts = None

        async def embed(self, texts):
            self.texts = texts
            return [[1.0, 0.0] for _ in texts]

    class Index:
        received = None

        def upsert(self, received_documents, embeddings):
            self.received = (received_documents, embeddings)
            return len(received_documents)

    embedding_client = Embeddings()
    index = Index()
    count = await VectorRetrievalService(
        repository=Repository(), embedding_client=embedding_client, vector_index=index
    ).index_city("杭州市")

    assert count == 1
    assert "西湖" in embedding_client.texts[0]
    assert "风景名胜" in embedding_client.texts[0]
    assert "慢节奏游览" in embedding_client.texts[0]
