from types import SimpleNamespace

import pytest

from backend.models.retrieval import HybridSearchResult
from backend.poi.enrichment import AmapMcpCandidateEnrichmentService


@pytest.mark.asyncio
async def test_candidate_enrichment_keeps_local_selection_fields_and_writes_facts_back():
    candidate = HybridSearchResult(
        poi_id="local-1",
        name="西湖",
        city="杭州市",
        interest_tags=["自然"],
        description="本地维护的慢节奏湖景描述",
        visit_duration_minutes=120,
        final_rank=1,
    )

    class Client:
        async def enrich_attractions(self, items):
            assert items[0].tags == ["自然"]
            assert items[0].description == "本地维护的慢节奏湖景描述"
            assert items[0].visit_duration_minutes == 120
            return [
                items[0].model_copy(
                    update={
                        "category": "湖泊",
                        "longitude": 120.148,
                        "latitude": 30.243,
                    }
                )
            ]

    class Repository:
        def __init__(self):
            self.saved = None

        def upsert_many(self, city_id, items):
            self.saved = (city_id, items)

    repository = Repository()
    service = AmapMcpCandidateEnrichmentService(
        client=Client(),
        attraction_repository=repository,
        catalog_repository=SimpleNamespace(
            find_city=lambda city: SimpleNamespace(id=7, name=city)
        ),
    )

    result = await service.enrich(city="杭州市", candidates=[candidate])

    assert result[0].category == "湖泊"
    assert result[0].longitude == 120.148
    assert result[0].latitude == 30.243
    assert result[0].interest_tags == ["自然"]
    assert result[0].description == "本地维护的慢节奏湖景描述"
    assert result[0].visit_duration_minutes == 120
    assert repository.saved[0] == 7
    assert repository.saved[1][0].poi_id == "local-1"
