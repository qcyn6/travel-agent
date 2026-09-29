import json

import httpx
import pytest

from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult
from backend.planner.attraction_details import DeepSeekAttractionDescriptionService


def _candidate(poi_id: str, name: str) -> HybridSearchResult:
    return HybridSearchResult(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        category="风景名胜",
        interest_tags=["自然"],
        description=f"本地资料：{name}适合自然体验。",
        visit_duration_minutes=120,
        preference_score=0.9,
        final_rank=1,
    )


@pytest.mark.asyncio
async def test_ai_attraction_details_replace_only_in_memory_descriptions():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "两层" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"attractions":['
                                '{"poi_id":"A1","name":"西湖",'
                                '"description":"湖山相映，适合散步、观景和拍照。"}]}'
                            )
                        }
                    }
                ]
            },
        )

    items = [_candidate("A1", "西湖"), _candidate("A2", "灵隐寺")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekAttractionDescriptionService(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
        ).enrich(
            city="杭州市",
            candidates=items,
            requirement=TravelRequirement(interests=["自然"], preference_text="节奏轻松"),
        )

    assert result[0].description == "湖山相映，适合散步、观景和拍照。"
    assert result[1].description == items[1].description
    assert result[0].poi_id == items[0].poi_id
    assert result[0].visit_duration_minutes == items[0].visit_duration_minutes
    assert items[0].description == "本地资料：西湖适合自然体验。"


@pytest.mark.asyncio
async def test_ai_attraction_details_fall_back_to_local_on_provider_failure():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "temporary"})

    items = [_candidate("A1", "西湖")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekAttractionDescriptionService(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
            max_retries=1,
        ).enrich(city="杭州市", candidates=items)

    assert result == items
