from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from backend.attraction_facts.extractor import DeepSeekAttractionFactsExtractor
from backend.attraction_facts.service import AttractionFactsEnrichmentService
from backend.models.attraction_facts import AttractionFacts, OpeningHours
from backend.models.travel_plan import AttractionStop


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": """```json
                        {"environment":"mixed","estimated_visit_minutes":180,
                        "opening_hours":{"status":"known","periods":[{"weekdays":[1,2,3,4,5,6,7],
                        "open_time":"08:30","close_time":"17:00","last_entry_time":"16:30"}],
                        "raw_text":"每日8:30至17:00开放，16:30停止入园", "applicability_text":"全年",
                        "confidence":"high","source_url":null,"checked_at":null},"confidence":"high"}
                        ```"""
                    }
                }
            ]
        }


class FakeClient:
    async def post(self, *args, **kwargs):
        return FakeResponse()


class OpeningOnlyResponse(FakeResponse):
    def json(self):
        body = super().json()
        body["choices"][0]["message"]["content"] = body["choices"][0]["message"]["content"].replace(
            '"estimated_visit_minutes":180', '"estimated_visit_minutes":null'
        )
        return body


class OpeningOnlyClient:
    async def post(self, *args, **kwargs):
        return OpeningOnlyResponse()


class MemoryFactsRepository:
    def __init__(self):
        self.items = {}

    def find(self, poi_id):
        return self.items.get(poi_id)

    def upsert(self, facts):
        self.items[facts.poi_id] = facts
        return facts


class FailingFetcher:
    async def fetch(self, url):
        raise httpx.RequestError("failed")


class WebErrorFetcher:
    async def fetch(self, url):
        from backend.knowledge.web_fetcher import WebContentError

        raise WebContentError("failed")


@pytest.mark.asyncio
async def test_extractor_returns_structured_opening_hours_and_duration():
    checked_at = datetime(2026, 9, 13, 12, 0)
    extractor = DeepSeekAttractionFactsExtractor(
        "test-key",
        base_url="https://example.com",
        model="test-model",
        client=FakeClient(),
        now_provider=lambda: checked_at,
    )

    facts = await extractor.extract(
        poi_id="A1",
        name="测试景点",
        city="杭州市",
        category="风景名胜",
        source_url="https://example.gov.cn/a1",
        source_text="这是足够长的测试网页正文，每日8:30至17:00开放，建议游玩3小时。",
    )

    assert facts.estimated_visit_minutes == 180
    assert facts.duration_source == "web_extracted"
    assert facts.opening_hours.status == "known"
    assert facts.opening_hours.periods[0].open_time == "08:30"
    assert facts.opening_hours.source_url == "https://example.gov.cn/a1"
    assert facts.checked_at == checked_at


@pytest.mark.asyncio
async def test_extractor_preserves_opening_hours_when_duration_uses_category_fallback():
    extractor = DeepSeekAttractionFactsExtractor(
        "test-key",
        base_url="https://example.com",
        model="test-model",
        client=OpeningOnlyClient(),
    )

    facts = await extractor.extract(
        poi_id="M1",
        name="城市博物馆",
        city="杭州市",
        category="博物馆",
        source_url="https://example.gov.cn/museum",
        source_text="网页明确给出了开放时间，但没有建议游览时长。",
    )

    assert facts.opening_hours.status == "known"
    assert facts.estimated_visit_minutes == 120
    assert facts.duration_source == "category_fallback"


@pytest.mark.asyncio
async def test_service_uses_safe_category_fallback_when_source_cannot_be_fetched():
    repository = MemoryFactsRepository()
    service = AttractionFactsEnrichmentService(
        repository=repository,
        knowledge_repository=SimpleNamespace(
            find=lambda poi_id: SimpleNamespace(source_url="https://example.gov.cn/museum")
        ),
        fetcher=WebErrorFetcher(),
        extractor=SimpleNamespace(),
        now_provider=lambda: datetime(2026, 9, 13, 12, 0),
    )
    stop = AttractionStop(poi_id="M1", name="城市博物馆", city="杭州市", category="博物馆")

    enriched = await service.enrich_stop(stop)

    assert enriched.environment == "indoor"
    assert enriched.estimated_visit_minutes == 120
    assert enriched.opening_hours.status == "unknown"
    assert repository.items["M1"].duration_source == "category_fallback"


@pytest.mark.asyncio
async def test_service_reuses_mysql_facts_without_fetching_source():
    checked_at = datetime(2026, 9, 13, 12, 0)
    repository = MemoryFactsRepository()
    repository.items["A1"] = AttractionFacts(
        poi_id="A1",
        environment="outdoor",
        estimated_visit_minutes=150,
        duration_source="category_fallback",
        opening_hours=OpeningHours(status="unknown", checked_at=checked_at),
        checked_at=checked_at,
    )
    service = AttractionFactsEnrichmentService(
        repository=repository,
        knowledge_repository=SimpleNamespace(find=lambda poi_id: None),
        fetcher=SimpleNamespace(),
        extractor=SimpleNamespace(),
    )

    enriched = await service.enrich_stop(
        AttractionStop(poi_id="A1", name="西湖", city="杭州市", category="湖")
    )

    assert enriched.estimated_visit_minutes == 150
    assert enriched.environment == "outdoor"
