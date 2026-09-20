import json

import httpx
import pytest

from backend.models.retrieval import AttractionDocument, VectorSearchResult
from backend.rag.bm25 import BM25Retriever, build_preference_query
from backend.rag.embedding import EmbeddingError
from backend.rag.fusion import reciprocal_rank_fusion
from backend.rag.hybrid import HybridRetrievalService
from backend.rag.interest import rank_interest_matches
from backend.rag.reranker import DashScopeReranker, DeepSeekReranker, RerankingError


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


def test_interest_matching_prefers_database_description_for_user_interest():
    documents = [
        doc("A1", "城市湿地公园", "自然生态丰富，适合休闲散步。"),
        doc("A2", "古城遗址博物馆", "保存历史文化遗存，适合了解当地人文。"),
    ]

    ranking = rank_interest_matches(documents, ["历史文化"], None, limit=2)

    assert ranking[0][0] == "A2"
    assert len(ranking) == 1
    assert ranking[0][1] > 0


def test_interest_matching_uses_explicit_attraction_tags():
    documents = [
        doc("A1", "城市公园", "适合日常游览。"),
        doc("A2", "古城景区", "适合慢节奏游览。").model_copy(
            update={"interest_tags": ["历史文化", "人文"]}
        ),
    ]

    ranking = rank_interest_matches(documents, ["历史文化"], None, limit=2)

    assert ranking[0][0] == "A2"


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
async def test_dashscope_reranker_maps_scores_to_poi_ids():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/compatible-api/v1/reranks"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-rerank"
        assert payload["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.92},
                    {"index": 0, "relevance_score": 0.31},
                ]
            },
        )

    candidates = [doc("A1", "古城", "历史文化"), doc("A2", "湿地", "休闲摄影")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DashScopeReranker(
            "test-key",
            base_url="https://dashscope.test/compatible-api/v1",
            client=client,
        ).rerank(query="轻松摄影", candidates=candidates)

    assert result == ["A2", "A1"]


@pytest.mark.asyncio
async def test_dashscope_reranker_rejects_incomplete_results():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.9}]},
        )

    candidates = [doc("A1", "古城", "历史文化"), doc("A2", "湿地", "休闲摄影")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RerankingError, match="未知或缺失"):
            await DashScopeReranker(
                "test-key",
                base_url="https://dashscope.test/compatible-api/v1",
                client=client,
            ).rerank(query="轻松摄影", candidates=candidates)


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
        doc("A1", "古城", "历史文化遗址").model_copy(
            update={"interest_tags": ["摄影"]}
        ),
        doc("A2", "湿地", "适合休闲散步和摄影").model_copy(
            update={"interest_tags": ["摄影"]}
        ),
    ]

    class Repository:
        def list_documents(self, city):
            assert city == "杭州市"
            return documents

    class VectorService:
        indexed = False
        query = None

        async def index_city(self, city):
            self.indexed = True
            return 2

        async def search(self, **kwargs):
            self.query = kwargs["query"]
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
    assert "摄影" not in vector_service.query
    assert [result.poi_id for result in results] == ["A2", "A1"]
    assert [result.final_rank for result in results] == [1, 2]


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_to_bm25_when_embedding_is_unavailable():
    documents = [
        doc("A1", "古城", "历史文化遗址").model_copy(
            update={"interest_tags": ["摄影"]}
        ),
        doc("A2", "湿地", "适合休闲散步和摄影").model_copy(
            update={"interest_tags": ["摄影"]}
        ),
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
    assert all(result.bm25_rank is None for result in results)
    assert results[0].keyword_score > 0


@pytest.mark.asyncio
async def test_hybrid_search_requires_interest_match_in_database_tags():
    documents = [
        doc("A1", "城市公园", "适合日常游览。"),
        doc("A2", "古城遗址", "保存历史文化遗存，适合了解当地人文。").model_copy(
            update={"interest_tags": ["历史文化"]}
        ),
    ]

    class Repository:
        def list_documents(self, city):
            return documents

    class FailingVectorService:
        async def index_city(self, city):
            raise EmbeddingError("network down")

    class NeutralReranker:
        async def rerank(self, **kwargs):
            return ["A1", "A2"]

    results = await HybridRetrievalService(
        repository=Repository(),
        vector_service=FailingVectorService(),
        reranker=NeutralReranker(),
    ).search(
        city="杭州市",
        interests=["历史文化"],
        preference_text=None,
        top_k=2,
    )

    assert [result.poi_id for result in results] == ["A2"]
    assert results[0].interest_rank == 1


@pytest.mark.asyncio
async def test_hybrid_search_uses_exact_catalog_tag_score_before_textual_match():
    documents = [
        doc("A1", "城市公园", "包含历史文化展陈，适合了解当地故事。").model_copy(
            update={"interest_tags": ["摄影"]}
        ),
        doc("A2", "古城遗址", "适合轻松游览。").model_copy(
            update={"interest_tags": ["历史文化", "人文"]}
        ),
    ]

    class Repository:
        def list_documents(self, city, *, interests):
            return documents

    class FailingVectorService:
        async def index_city(self, city):
            raise EmbeddingError("network down")

    class NeutralReranker:
        async def rerank(self, **kwargs):
            return ["A1", "A2"]

    results = await HybridRetrievalService(
        repository=Repository(),
        vector_service=FailingVectorService(),
        reranker=NeutralReranker(),
    ).search(
        city="杭州市",
        interests=["历史文化"],
        preference_text=None,
        top_k=2,
    )

    assert [result.poi_id for result in results] == ["A2"]


@pytest.mark.asyncio
async def test_hybrid_search_preserves_database_duration():
    documents = [
        doc("A1", "城市公园", "适合自然观景和摄影。").model_copy(
            update={"interest_tags": ["自然"]}
        ),
        doc("A2", "博物馆", "适合了解历史文化。").model_copy(
            update={
                "interest_tags": ["历史文化"],
                "visit_duration_minutes": 180,
            }
        ),
    ]

    class Repository:
        def list_documents(self, city, *, interests):
            return documents

    class FailingVectorService:
        async def index_city(self, city):
            raise EmbeddingError("network down")

    class NeutralReranker:
        async def rerank(self, **kwargs):
            return ["A1", "A2"]

    results = await HybridRetrievalService(
        repository=Repository(),
        vector_service=FailingVectorService(),
        reranker=NeutralReranker(),
    ).search(
        city="杭州市",
        interests=["历史文化"],
        preference_text=None,
        top_k=2,
    )

    stored = next(result for result in results if result.poi_id == "A2")
    assert stored.visit_duration_minutes == 180


@pytest.mark.asyncio
async def test_hybrid_search_combines_preference_semantic_and_description_keywords():
    documents = [
        doc("A1", "湖光山色", "适合欣赏山水景色。").model_copy(
            update={"interest_tags": ["自然"]}
        ),
        doc("A2", "城市湿地", "适合轻松散步和拍照。").model_copy(
            update={"interest_tags": ["自然"]}
        ),
    ]

    class Repository:
        def list_documents(self, city, *, interests):
            return documents

    class VectorService:
        def __init__(self):
            self.query = None

        async def index_city(self, city):
            return 0

        async def search(self, **kwargs):
            self.query = kwargs["query"]
            return [
                VectorSearchResult(**documents[1].model_dump(), distance=0.1),
                VectorSearchResult(**documents[0].model_dump(), distance=0.3),
            ]

    vector_service = VectorService()
    results = await HybridRetrievalService(
        repository=Repository(), vector_service=vector_service
    ).search(
        city="杭州市",
        interests=["自然"],
        preference_text="轻松散步",
        top_k=2,
    )

    assert "自然" not in vector_service.query
    assert [result.poi_id for result in results] == ["A2", "A1"]
    assert results[0].keyword_score == pytest.approx(1.0)
    assert results[0].preference_score == pytest.approx(0.93)


@pytest.mark.asyncio
async def test_hybrid_search_returns_empty_when_no_interest_tag_matches():
    documents = [
        doc("A1", "城市公园", "描述中提到自然风光，但没有对应标签。"),
    ]

    class Repository:
        def list_documents(self, city, *, interests):
            return documents

    class VectorService:
        async def index_city(self, city):
            raise AssertionError("tag filtering should stop before vector search")

    results = await HybridRetrievalService(
        repository=Repository(), vector_service=VectorService()
    ).search(
        city="杭州市",
        interests=["自然"],
        preference_text="轻松游览",
        top_k=5,
    )

    assert results == []


@pytest.mark.asyncio
async def test_planning_search_can_keep_every_tag_matched_attraction():
    documents = [
        doc(f"A{index}", f"自然景点{index}", f"第{index}个自然景点。")
        .model_copy(update={"interest_tags": ["自然"]})
        for index in range(25)
    ]

    class Repository:
        def list_documents(self, city, *, interests):
            return documents

    class UnusedVectorService:
        async def index_city(self, city):
            raise AssertionError("没有自由偏好时不应调用向量服务")

    results = await HybridRetrievalService(
        repository=Repository(), vector_service=UnusedVectorService()
    ).search(
        city="杭州市",
        interests=["自然"],
        preference_text=None,
        top_k=None,
    )

    assert len(results) == 25
    assert all(result.tag_match_count == 1 for result in results)
    assert all(result.preference_score == 0 for result in results)
