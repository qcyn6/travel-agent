import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.session import Base
from backend.rag.embedding import EmbeddingConfigurationError
from backend.rag.knowledge_service import KnowledgeRetrievalService
from backend.rag.knowledge_source import KnowledgeChunk, MarkdownKnowledgeSource
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


def test_markdown_source_loads_all_project_province_and_city_chunks():
    documents = MarkdownKnowledgeSource("data/knowledge").list_documents()

    assert len(documents) == 406
    assert sum(item.entity_type == "province" for item in documents) == 34
    assert sum(item.entity_type == "city" for item in documents) == 372
    assert documents[0].doc_id == "province:01"
    assert documents[-1].doc_id == "city:34-01"


def test_chroma_knowledge_index_round_trips_metadata_and_content(tmp_path):
    documents = [
        chunk("city:01-01", "city", "北京市", "故宫和胡同适合历史文化旅行。"),
        chunk("city:02-01", "city", "天津市", "海河和五大道适合城市漫步。"),
    ]
    index = ChromaKnowledgeIndex(str(tmp_path / "chroma"), "test_travel_knowledge")

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
async def test_general_service_can_receive_rag_context(database_session):
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

    assert prepared.destination_context == "【城市知识：北京市】\n故宫和胡同适合历史文化旅行。"
    assert retriever.calls[0][0] == "介绍一下北京的历史文化"
