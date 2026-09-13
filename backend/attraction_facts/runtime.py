from backend.attraction_facts.extractor import DeepSeekAttractionFactsExtractor
from backend.attraction_facts.repository import AttractionFactsRepository
from backend.attraction_facts.service import AttractionFactsEnrichmentService
from backend.knowledge.web_fetcher import WebPageFetcher


def build_attraction_facts_service(*, session, knowledge_repository, settings):
    return AttractionFactsEnrichmentService(
        repository=AttractionFactsRepository(session),
        knowledge_repository=knowledge_repository,
        fetcher=WebPageFetcher(),
        extractor=DeepSeekAttractionFactsExtractor(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
