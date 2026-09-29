import json

import httpx
import pytest

from backend.rag.preference_rewriter import (
    PreferenceQueryRewriter,
    PreferenceRewriteError,
)


@pytest.mark.asyncio
async def test_preference_rewriter_returns_semantic_query_and_description_terms():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        user_input = json.loads(payload["messages"][1]["content"])
        assert user_input == {
            "preference_text": "不想太累",
            "candidate_descriptions": [
                {
                    "name": "湖滨公园",
                    "category": "风景名胜",
                    "description": "沿湖有平缓步道，适合轻松漫步。",
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "semantic_query": "轻松慢游、步行负担较低、路线平缓、可休息观景",
                                    "keyword_terms": ["平缓步道", "轻松漫步", "轻松漫步"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PreferenceQueryRewriter(
            "test-key",
            base_url="https://llm.test/v1",
            model="test-model",
            client=client,
        ).rewrite(
            "不想太累",
            candidate_descriptions=[
                {
                    "name": "湖滨公园",
                    "category": "风景名胜",
                    "description": "沿湖有平缓步道，适合轻松漫步。",
                }
            ],
        )

    assert result.semantic_query == "轻松慢游、步行负担较低、路线平缓、可休息观景"
    assert result.keyword_terms == ["平缓步道", "轻松漫步"]


@pytest.mark.asyncio
async def test_preference_rewriter_rejects_invalid_output():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"semantic_query": ""}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rewriter = PreferenceQueryRewriter(
            "test-key",
            base_url="https://llm.test",
            model="test-model",
            client=client,
        )
        with pytest.raises(PreferenceRewriteError, match="格式异常"):
            await rewriter.rewrite("不想太累", candidate_descriptions=[])
