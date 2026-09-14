import asyncio
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
        active = 0
        max_active = 0

        async def enrich(self, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0)
                if kwargs["poi_id"] == "A1":
                    raise RuntimeError("source failed")
                repository.ready += 1
            finally:
                self.active -= 1

    enrichment = Enrichment()
    progress_updates = []

    async def progress(*args):
        progress_updates.append(args)

    result = await CityKnowledgeBootstrapService(
        poi_service=POIService(),
        attraction_repository=repository,
        enrichment_service=enrichment,
    ).ensure(city=city, target_ready=3, max_attempts=4, progress=progress)

    assert result.ready_count == 3
    assert result.attempted_count == 4
    assert result.failed_count == 1
    assert enrichment.max_active == 3
    assert progress_updates[0] == (0, 3, 0, 4, 0)
    assert progress_updates[-1] == (3, 3, 4, 4, 1)


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


@pytest.mark.asyncio
async def test_bootstrap_retries_candidates_that_failed_from_network_errors():
    city = SimpleNamespace(id=1, name="宝鸡市")
    candidate = SimpleNamespace(
        poi_id="A1",
        name="关山草原景区",
        category="景区",
        rating=4.7,
        knowledge_status="failed",
        knowledge_error="权威来源搜索服务网络请求失败",
    )

    class Repository:
        ready = 0

        def count_ready_for_city(self, city_id):
            return self.ready

        def list_for_city(self, city_id, *, statuses=None, limit=30):
            return [candidate]

    repository = Repository()

    class Enrichment:
        async def enrich(self, **kwargs):
            repository.ready += 1

    result = await CityKnowledgeBootstrapService(
        poi_service=None,
        attraction_repository=repository,
        enrichment_service=Enrichment(),
    ).ensure(city=city, target_ready=1, max_attempts=1)

    assert result.ready_count == 1
    assert result.attempted_count == 1
