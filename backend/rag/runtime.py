from backend.rag.embedding import OpenAICompatibleEmbeddingClient
from backend.rag.hybrid import HybridRetrievalService
from backend.rag.knowledge_service import KnowledgeRetrievalService
from backend.rag.knowledge_source import TravelGuideKnowledgeSource
from backend.rag.knowledge_vector_store import ChromaKnowledgeIndex
from backend.rag.preference_rewriter import PreferenceQueryRewriter
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


def build_guide_retrieval_service(settings) -> KnowledgeRetrievalService:
    """Build the future-facing travel-topic RAG separately from destinations."""

    embedding_client = OpenAICompatibleEmbeddingClient(
        settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=settings.knowledge_embedding_batch_size,
    )
    return KnowledgeRetrievalService(
        source=TravelGuideKnowledgeSource(settings.guide_knowledge_path),
        embedding_client=embedding_client,
        vector_index=ChromaKnowledgeIndex(
            settings.chroma_path,
            collection_name=settings.guide_knowledge_chroma_collection,
        ),
        filter_destinations=False,
    )


def build_hybrid_service(repository, settings) -> HybridRetrievalService:
    preference_rewriter = None
    if getattr(settings, "llm_api_key", None):
        preference_rewriter = PreferenceQueryRewriter(
            settings.llm_api_key,
            base_url=getattr(settings, "llm_base_url", "https://api.deepseek.com"),
            model=getattr(settings, "llm_model", "deepseek-flash"),
            # This is an optional retrieval enhancement. Keep its added
            # latency bounded and fall back to local expansions on failure.
            timeout_seconds=min(
                float(getattr(settings, "llm_timeout_seconds", 5.0)),
                5.0,
            ),
        )
    return HybridRetrievalService(
        repository=repository,
        vector_service=build_vector_service(repository, settings),
        preference_rewriter=preference_rewriter,
    )
