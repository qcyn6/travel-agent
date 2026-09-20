from backend.rag.embedding import OpenAICompatibleEmbeddingClient
from backend.rag.hybrid import HybridRetrievalService
from backend.rag.knowledge_service import KnowledgeRetrievalService
from backend.rag.knowledge_source import MarkdownKnowledgeSource
from backend.rag.knowledge_vector_store import ChromaKnowledgeIndex
from backend.rag.service import VectorRetrievalService
from backend.rag.vector_store import ChromaAttractionIndex


def build_vector_service(repository, settings) -> VectorRetrievalService:
    embedding_client = OpenAICompatibleEmbeddingClient(
        settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    return VectorRetrievalService(
        repository=repository,
        embedding_client=embedding_client,
        vector_index=ChromaAttractionIndex(
            settings.chroma_path,
            collection_name=settings.chroma_collection,
        ),
    )


def build_knowledge_retrieval_service(settings) -> KnowledgeRetrievalService:
    embedding_client = OpenAICompatibleEmbeddingClient(
        settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=settings.knowledge_embedding_batch_size,
    )
    return KnowledgeRetrievalService(
        source=MarkdownKnowledgeSource(settings.knowledge_path),
        embedding_client=embedding_client,
        vector_index=ChromaKnowledgeIndex(
            settings.chroma_path,
            collection_name=settings.knowledge_chroma_collection,
        ),
    )


def build_hybrid_service(repository, settings) -> HybridRetrievalService:
    return HybridRetrievalService(
        repository=repository,
        vector_service=build_vector_service(repository, settings),
    )
