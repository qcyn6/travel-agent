from datetime import datetime
import logging
from typing import Protocol

from backend.attraction_facts.defaults import category_defaults
from backend.attraction_facts.extractor import FactsExtractionError
from backend.knowledge.web_fetcher import WebContentError
from backend.models.attraction_facts import AttractionFacts, OpeningHours
from backend.models.travel_plan import AttractionStop, TravelDay

logger = logging.getLogger(__name__)


class FactsStore(Protocol):
    def find(self, poi_id: str) -> AttractionFacts | None: ...
    def upsert(self, facts: AttractionFacts) -> AttractionFacts: ...


class KnowledgeSourceStore(Protocol):
    def find(self, poi_id: str): ...


class AttractionFactsEnrichmentService:
    def __init__(
        self,
        *,
        repository: FactsStore,
        knowledge_repository: KnowledgeSourceStore,
        fetcher,
        extractor,
        now_provider=datetime.now,
    ):
        self.repository = repository
        self.knowledge_repository = knowledge_repository
        self.fetcher = fetcher
        self.extractor = extractor
        self.now_provider = now_provider

    async def enrich_day(self, day: TravelDay) -> TravelDay:
        enriched = [await self.enrich_stop(stop) for stop in day.attractions]
        return day.model_copy(update={"attractions": enriched})

    async def enrich_stop(self, stop: AttractionStop, *, force_refresh: bool = False) -> AttractionStop:
        if not force_refresh:
            existing = self.repository.find(stop.poi_id)
            if existing is not None:
                return _apply_facts(stop, existing)

        source = self.knowledge_repository.find(stop.poi_id)
        source_url = source.source_url if source is not None else None
        facts = None
        if source_url:
            try:
                source_text = await self.fetcher.fetch(source_url)
                facts = await self.extractor.extract(
                    poi_id=stop.poi_id,
                    name=stop.name,
                    city=stop.city,
                    category=stop.category,
                    source_url=source_url,
                    source_text=source_text,
                )
            except (WebContentError, FactsExtractionError) as exc:
                logger.warning(
                    "景点事实无法从网页提取，使用类别时长且开放时间标记未知：%s (%s)",
                    stop.poi_id,
                    type(exc).__name__,
                )
                facts = None
        if facts is None:
            facts = _fallback_facts(stop, source_url=source_url, checked_at=self.now_provider())
        return _apply_facts(stop, self.repository.upsert(facts))


def _apply_facts(stop: AttractionStop, facts: AttractionFacts) -> AttractionStop:
    return stop.model_copy(
        update={
            "environment": facts.environment,
            "estimated_visit_minutes": facts.estimated_visit_minutes,
            "duration_source": facts.duration_source,
            "opening_hours": facts.opening_hours,
        }
    )


def _fallback_facts(
    stop: AttractionStop,
    *,
    source_url: str | None,
    checked_at: datetime,
) -> AttractionFacts:
    duration, environment = category_defaults(stop.category, stop.name)
    return AttractionFacts(
        poi_id=stop.poi_id,
        environment=environment,
        estimated_visit_minutes=duration,
        duration_source="category_fallback",
        opening_hours=OpeningHours(
            status="unknown",
            confidence="unknown",
            source_url=source_url,
            checked_at=checked_at,
        ),
        source_url=source_url,
        confidence="low",
        checked_at=checked_at,
    )
