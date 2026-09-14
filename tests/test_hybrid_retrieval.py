import httpx
import pytest

from backend.models.retrieval import AttractionDocument, VectorSearchResult
from backend.rag.bm25 import BM25Retriever, build_preference_query
from backend.rag.embedding import EmbeddingError
from backend.rag.fusion import reciprocal_rank_fusion
from backend.rag.hybrid import HybridRetrievalService
from backend.rag.reranker import DeepSeekReranker, RerankingError


def doc(poi_id: str, name: str, description: str) -> AttractionDocument:
    return AttractionDocument(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        category="风景名胜",
        description=description,
        source_url="https://example.gov.cn/source",
    )


def test_preference_query_expands_explicit_words():
    query = build_preference_query(["历史文化", "摄影"], "希望轻松散步和拍照")

    assert all(word in query for word in ["历史", "人文", "取景", "休闲", "慢游", "漫步", "步行"])


def test_bm25_ranks_matching_attraction_first():
    documents = [
        doc("A1", "古城遗址", "拥有深厚历史文化，适合了解古代文明和人文遗产。"),
        doc("A2", "湿地公园", "自然生态丰富，适合观鸟和休闲散步。"),
    ]

    ranking = BM25Retriever().rank(documents, "历史文化 人文 遗址", limit=2)

    assert ranking[0][0] == "A1"


def test_rrf_rewards_items_present_in_both_rankings():
    fused = reciprocal_rank_fusion([["A1", "A2"], ["A2", "A3"]])

    assert fused[0][0] == "A2"
    assert {poi_id for poi_id, _ in fused} == {"A1", "A2", "A3"}


@pytest.mark.asyncio
async def test_deepseek_reranker_accepts_only_complete_known_ids():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "```json\n[\"A2\", \"A1\"]\n```"}}]},
        )

    candidates = [doc("A1", "古城", "历史文化"), doc("A2", "湿地", "休闲摄影")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekReranker(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
        ).rerank(query="轻松摄影", candidates=candidates)

    assert result == ["A2", "A1"]


@pytest.mark.asyncio
async def test_deepseek_reranker_rejects_hallucinated_id():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[\"UNKNOWN\", \"A1\"]"}}]},
        )

    candidates = [doc("A1", "古城", "历史文化"), doc("A2", "湿地", "休闲摄影")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RerankingError, match="未知或缺失"):
            await DeepSeekReranker(
                "test-key",
                base_url="https://deepseek.test",
                model="test-model",
                client=client,
            ).rerank(query="轻松摄影", candidates=candidates)


@pytest.mark.asyncio
async def test_hybrid_search_uses_reranked_order_and_city_documents():
    documents = [
        doc("A1", "古城", "历史文化遗址"),
        doc("A2", "湿地", "适合休闲散步和摄影"),
    ]

    class Repository:
        def list_documents(self, city):
            assert city == "杭州市"
            return documents

    class VectorService:
        indexed = False

        async def index_city(self, city):
            self.indexed = True
            return 2

        async def search(self, **kwargs):
            return [
                VectorSearchResult(
                    **documents[1].model_dump(), distance=0.1
                ),
                VectorSearchResult(
                    **documents[0].model_dump(), distance=0.2
                ),
            ]

    class Reranker:
        async def rerank(self, **kwargs):
            return ["A2", "A1"]

    vector_service = VectorService()
    results = await HybridRetrievalService(
        repository=Repository(), vector_service=vector_service, reranker=Reranker()
    ).search(
        city="杭州市",
        interests=["摄影"],
        preference_text="轻松散步",
        top_k=2,
    )

    assert vector_service.indexed is True
    assert [result.poi_id for result in results] == ["A2", "A1"]
    assert [result.final_rank for result in results] == [1, 2]


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_to_bm25_when_embedding_is_unavailable():
    documents = [
        doc("A1", "古城", "历史文化遗址"),
        doc("A2", "湿地", "适合休闲散步和摄影"),
    ]

    class Repository:
        def list_documents(self, city):
            return documents

    class FailingVectorService:
        async def index_city(self, city):
            raise EmbeddingError("network down")

        async def search(self, **kwargs):
            raise AssertionError("search should not run after index failure")

    class Reranker:
        async def rerank(self, **kwargs):
            return [item.poi_id for item in kwargs["candidates"]]

    results = await HybridRetrievalService(
        repository=Repository(),
        vector_service=FailingVectorService(),
        reranker=Reranker(),
    ).search(
        city="杭州市",
        interests=["摄影"],
        preference_text="轻松散步",
        top_k=2,
    )

    assert results
    assert all(result.vector_rank is None for result in results)
    assert all(result.bm25_rank is not None for result in results)
