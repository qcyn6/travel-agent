import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.session import Base
from backend.rag.embedding import EmbeddingConfigurationError
from backend.rag.knowledge_service import KnowledgeRetrievalService
from backend.rag.knowledge_source import (
    KnowledgeChunk,
    TravelGuideKnowledgeSource,
)
from backend.rag.knowledge_vector_store import ChromaKnowledgeIndex


@pytest.fixture
def database_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def chunk(doc_id: str, entity_type: str, name: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        doc_id=doc_id,
        entity_type=entity_type,
        name=name,
        province="北京市",
        region_level=entity_type,
        source_file="test.md",
        content=content,
    )


def test_travel_guide_source_loads_topic_sections_separately():
    documents = TravelGuideKnowledgeSource("data/knowledge/guides").list_documents()

    assert len(documents) >= 30
    assert all(document.entity_type == "guide" for document in documents)
    assert all(document.source_file != "README.md" for document in documents)
    assert any("高原" in document.content for document in documents)
    assert any("慢节奏" in document.content for document in documents)
    assert any("徒步" in document.content for document in documents)
    assert any("行前准备" in document.content for document in documents)


def test_travel_guide_source_exposes_keywords_for_retrieval_routing():
    source = TravelGuideKnowledgeSource("data/knowledge/guides")
    documents = source.list_documents()
    keywords = source.retrieval_keywords()

    high_temperature = next(
        document for document in documents if document.source_file == "高温旅行注意事项.md"
    )
    assert "炎热" in high_temperature.keywords
    assert "炎热" in keywords
    assert "证件" in keywords
    assert len(keywords) == len(set(keywords))


def test_chroma_knowledge_index_round_trips_metadata_and_content(tmp_path):
    documents = [
        chunk("city:01-01", "city", "北京市", "故宫和胡同适合历史文化旅行。"),
        chunk("city:02-01", "city", "天津市", "海河和五大道适合城市漫步。"),
    ]
    index = ChromaKnowledgeIndex(str(tmp_path / "chroma"), "test_travel_guides")

    assert index.upsert(documents, [[1.0, 0.0], [0.0, 1.0]]) == 2
    results = index.search(query_embedding=[0.0, 1.0], limit=1)

    assert results[0].doc_id == "city:02-01"
    assert results[0].name == "天津市"
    assert "海河" in results[0].content


@pytest.mark.asyncio
async def test_knowledge_retrieval_falls_back_to_bm25_without_embedding():
    documents = [
        chunk("city:01-01", "city", "北京市", "故宫、长城和胡同适合历史文化旅行。"),
        chunk("city:02-01", "city", "天津市", "海河、五大道和相声适合城市漫步。"),
    ]

    class Source:
        def list_documents(self):
            return documents

    class Embeddings:
        async def embed(self, texts):
            raise EmbeddingConfigurationError("missing key")

    class VectorIndex:
        def search(self, **kwargs):
            raise AssertionError("embedding failure should skip vector search")

    results = await KnowledgeRetrievalService(
        source=Source(),
        embedding_client=Embeddings(),
        vector_index=VectorIndex(),
    ).search("故宫 历史文化", destination_names=["北京市"], limit=2)

    assert [result.name for result in results] == ["北京市"]
    assert "故宫" in results[0].content


@pytest.mark.asyncio
async def test_destination_knowledge_is_left_to_the_agent_without_pre_retrieval(database_session):
    from backend.conversation.general_repository import GeneralConversationRepository
    from backend.conversation.general_service import GeneralConversationService

    class Retriever:
        calls = []

        async def context(self, message, *, destination_names):
            self.calls.append((message, destination_names))
            return "【城市知识：北京市】\n故宫和胡同适合历史文化旅行。"

    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    retriever = Retriever()
    service = GeneralConversationService(
        repository=repository,
        knowledge_retriever=retriever,
    )

    prepared = await service.prepare_message(conversation.id, "介绍一下北京的历史文化")

    assert prepared.intent == "general"
    assert prepared.retrieval_sufficient is None
    assert prepared.destination_context is None
    assert retriever.calls == []


@pytest.mark.asyncio
async def test_adaptive_context_rewrites_once_when_first_retrieval_is_empty():
    from backend.models.retrieval import KnowledgeSearchResult

    class Retriever(KnowledgeRetrievalService):
        def __init__(self):
            self.calls = []

        async def search(self, query, *, limit=4, destination_names=None):
            self.calls.append(query)
            if "摄影" not in query:
                return []
            return [
                KnowledgeSearchResult(
                    doc_id="city:01-01",
                    entity_type="city",
                    name="北京市",
                    province="北京市",
                    region_level="city",
                    source_file="test.md",
                    content="故宫和胡同适合摄影与历史文化旅行。",
                    distance=0.0,
                )
            ]

    retriever = Retriever()
    result = await retriever.adaptive_context(
        "请介绍一下北京适合拍照的地方",
        destination_names=["北京市"],
    )

    assert result.sufficient is True
    assert result.attempts == 2
    assert "摄影" in result.query
    assert len(retriever.calls) == 2


@pytest.mark.asyncio
async def test_travel_guides_use_bm25_when_embedding_is_unavailable():
    class Embeddings:
        async def embed(self, texts):
            raise EmbeddingConfigurationError("missing key")

    class VectorIndex:
        def search(self, **kwargs):
            raise AssertionError("embedding failure should skip vector search")

    service = KnowledgeRetrievalService(
        source=TravelGuideKnowledgeSource("data/knowledge/guides"),
        embedding_client=Embeddings(),
        vector_index=VectorIndex(),
        filter_destinations=False,
    )
    result = await service.adaptive_context("高原旅行注意事项")

    assert result.sufficient is True
    assert result.attempts == 1
    assert result.context is not None
    assert "高原" in result.context
