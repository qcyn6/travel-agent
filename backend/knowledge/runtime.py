from backend.knowledge.bootstrap import CityKnowledgeBootstrapService
from backend.knowledge.service import KnowledgeEnrichmentService
from backend.knowledge.sources import (
    CuratedSourceDiscovery,
    NationwideSourceDiscovery,
    TavilyGovernmentSourceDiscovery,
)
from backend.knowledge.summarizer import DeepSeekSummarizer
from backend.knowledge.web_fetcher import WebPageFetcher
from backend.poi import AmapAttractionClient
from backend.services import AttractionService


def build_enrichment_service(
    *, attraction_repository, knowledge_repository, settings, cache=None
) -> KnowledgeEnrichmentService:
    return KnowledgeEnrichmentService(
        sources=NationwideSourceDiscovery(
            curated=CuratedSourceDiscovery(),
            web_search=TavilyGovernmentSourceDiscovery(
                settings.web_search_api_key,
                base_url=settings.web_search_base_url,
                timeout_seconds=settings.web_search_timeout_seconds,
                max_retries=settings.web_search_max_retries,
                cache=cache,
                cache_ttl_seconds=settings.redis_search_ttl_seconds,
            ),
        ),
        fetcher=WebPageFetcher(),
        summarizer=DeepSeekSummarizer(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        repository=knowledge_repository,
        status_store=attraction_repository,
    )


def build_bootstrap_service(
    *, attraction_repository, knowledge_repository, settings, cache=None
) -> CityKnowledgeBootstrapService:
    poi_client = AmapAttractionClient(
        settings.amap_api_key,
        base_url=settings.amap_base_url,
        timeout_seconds=settings.amap_timeout_seconds,
        max_retries=settings.amap_max_retries,
        cache=cache,
        cache_ttl_seconds=settings.redis_poi_ttl_seconds,
    )
    return CityKnowledgeBootstrapService(
        poi_service=AttractionService(poi_client, attraction_repository),
        attraction_repository=attraction_repository,
        enrichment_service=build_enrichment_service(
            attraction_repository=attraction_repository,
            knowledge_repository=knowledge_repository,
            settings=settings,
            cache=cache,
        ),
        max_concurrency=settings.knowledge_max_concurrency,
    )
