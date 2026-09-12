from typing import Protocol

from backend.knowledge.sources import DiscoveredSource
from backend.knowledge.web_fetcher import WebContentError
from backend.models.knowledge import AttractionKnowledge


class KnowledgeStore(Protocol):
    def find(self, poi_id: str) -> AttractionKnowledge | None: ...
    def upsert(self, *, poi_id: str, description: str, source_url: str) -> AttractionKnowledge: ...


class KnowledgeStatusStore(Protocol):
    def mark_knowledge_status(self, poi_id: str, status: str, error: str | None = None) -> None: ...


class KnowledgeEnrichmentService:
    def __init__(self, *, sources, fetcher, summarizer, repository: KnowledgeStore, status_store=None):
        self.sources = sources
        self.fetcher = fetcher
        self.summarizer = summarizer
        self.repository = repository
        self.status_store: KnowledgeStatusStore | None = status_store

    async def enrich(
        self,
        *,
        poi_id: str,
        name: str,
        city: str,
        category: str | None,
        force_refresh: bool = False,
    ) -> AttractionKnowledge:
        if not force_refresh:
            existing = self.repository.find(poi_id)
            if existing is not None:
                self._mark(poi_id, "ready")
                return existing

        self._mark(poi_id, "processing")
        try:
            discovered = await self.sources.find_source(name, city)
            if isinstance(discovered, str):
                discovered = DiscoveredSource(url=discovered)
            try:
                source_text = await self.fetcher.fetch(discovered.url)
            except WebContentError:
                if not discovered.extracted_text or len(discovered.extracted_text) < 80:
                    raise
                source_text = discovered.extracted_text
            description = await self.summarizer.summarize(
                name=name,
                city=city,
                category=category,
                source_text=source_text,
            )
            result = self.repository.upsert(
                poi_id=poi_id,
                description=description,
                source_url=discovered.url,
            )
        except Exception as exc:
            self._mark(poi_id, "failed", str(exc))
            raise
        self._mark(poi_id, "ready")
        return result

    def _mark(self, poi_id: str, status: str, error: str | None = None) -> None:
        if self.status_store is not None:
            self.status_store.mark_knowledge_status(poi_id, status, error)
