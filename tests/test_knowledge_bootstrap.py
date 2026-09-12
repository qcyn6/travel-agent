from types import SimpleNamespace

import pytest

from backend.knowledge.bootstrap import CityKnowledgeBootstrapService, KnowledgeBootstrapError


@pytest.mark.asyncio
async def test_bootstrap_fetches_pois_and_continues_after_failed_candidate():
    city = SimpleNamespace(id=1, name="昆明市")
    candidates = [
        SimpleNamespace(poi_id="A1", name="失败景点", category="景区"),
        SimpleNamespace(poi_id="A2", name="滇池", category="景区"),
        SimpleNamespace(poi_id="A3", name="石林", category="景区"),
        SimpleNamespace(poi_id="A4", name="民族村", category="景区"),
    ]

    class Repository:
        fetched = False
        ready = 0

        def count_ready_for_city(self, city_id):
            return self.ready

        def list_for_city(self, city_id, *, statuses=None, limit=30):
            if statuses is None and not self.fetched:
                return []
            return candidates[:limit]

    repository = Repository()

    class POIService:
        async def search_and_store(self, **kwargs):
            repository.fetched = True
            return []

    class Enrichment:
        async def enrich(self, **kwargs):
            if kwargs["poi_id"] == "A1":
                raise RuntimeError("source failed")
            repository.ready += 1

    result = await CityKnowledgeBootstrapService(
        poi_service=POIService(),
        attraction_repository=repository,
        enrichment_service=Enrichment(),
    ).ensure(city=city, target_ready=3, max_attempts=4)

    assert result.ready_count == 3
    assert result.attempted_count == 4
    assert result.failed_count == 1


@pytest.mark.asyncio
async def test_bootstrap_fails_when_minimum_knowledge_cannot_be_reached():
    city = SimpleNamespace(id=1, name="昆明市")

    class Repository:
        def count_ready_for_city(self, city_id):
            return 0

        def list_for_city(self, city_id, *, statuses=None, limit=30):
            return [SimpleNamespace(poi_id="A1", name="失败景点", category=None)]

    class Enrichment:
        async def enrich(self, **kwargs):
            raise RuntimeError("failed")

    with pytest.raises(KnowledgeBootstrapError, match="仅准备好 0 条"):
        await CityKnowledgeBootstrapService(
            poi_service=None,
            attraction_repository=Repository(),
            enrichment_service=Enrichment(),
        ).ensure(city=city, target_ready=3, max_attempts=1)
