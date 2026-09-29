import httpx
import pytest

from backend.models.retrieval import HybridSearchResult
from backend.planner.attraction_photos import AmapAttractionPhotoService


class MemoryCache:
    def __init__(self):
        self.values = {}

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, *, ttl_seconds):
        self.values[key] = value
        return True


def candidate(poi_id="A1", name="西湖"):
    return HybridSearchResult(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        category="景区",
        description="测试景点",
        final_rank=1,
    )


@pytest.mark.asyncio
async def test_photo_service_uses_exact_poi_match_and_caches_result():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {"name": "西湖文化广场", "photos": [{"url": "https://example.com/wrong.jpg"}]},
                    {"name": "西湖", "photos": [{"url": "https://example.com/west-lake.jpg"}]},
                ],
            },
        )

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AmapAttractionPhotoService(
            "test-key",
            client=client,
            cache=cache,
        )
        first = await service.enrich(city="杭州市", candidates=[candidate()])
        second = await service.enrich(city="杭州市", candidates=[candidate()])

    assert first == second == {"A1": "https://example.com/west-lake.jpg"}
    assert len(calls) == 1
    assert calls[0].url.params["show_fields"] == "photos"
    assert calls[0].url.params["city_limit"] == "true"


@pytest.mark.asyncio
async def test_photo_service_rejects_non_exact_or_unsafe_photo_url():
    async def handler(_request):
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {"name": "灵隐寺", "photos": [{"url": "javascript:alert(1)"}]},
                    {"name": "西湖景区", "photos": [{"url": "https://example.com/not-exact.jpg"}]},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AmapAttractionPhotoService("test-key", client=client)
        result = await service.enrich(city="杭州市", candidates=[candidate()])

    assert result == {}
