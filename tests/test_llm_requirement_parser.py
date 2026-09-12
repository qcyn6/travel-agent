import httpx
import pytest

from backend.requirements import (
    DeepSeekRequirementParser,
    RequirementLLMError,
    RequirementParser,
    RequirementParsingService,
)


@pytest.mark.asyncio
async def test_deepseek_requirement_parser_returns_contract_only():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "```json\n"
                            '{"province":"云南省","cities":[],"days":7,'
                            '"interests":["自然","古城","摄影"],'
                            '"preference_text":null,"travel_scope":"multi_city"}'
                            "\n```"
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekRequirementParser(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
        ).parse("云南玩七天，想去两三个城市")

    assert result.province == "云南省"
    assert result.days == 7
    assert result.travel_scope == "multi_city"


@pytest.mark.asyncio
async def test_requirement_service_falls_back_when_llm_is_unavailable():
    class FailedLLM:
        async def parse(self, text):
            raise RequirementLLMError("temporary")

    result = await RequirementParsingService(
        llm_parser=FailedLLM(), fallback_parser=RequirementParser()
    ).parse("杭州玩一天，喜欢拍照和历史文化")

    assert result.cities == ["杭州"]
    assert result.days == 1


@pytest.mark.asyncio
async def test_requirement_service_normalizes_synonyms_and_fills_explicit_preference():
    class LLM:
        async def parse(self, text):
            from backend.models.requirement import TravelRequirement

            return TravelRequirement(
                cities=["杭州"],
                days=3,
                interests=["拍照"],
                preference_text=None,
                travel_scope="single_city",
            )

    result = await RequirementParsingService(
        llm_parser=LLM(), fallback_parser=RequirementParser()
    ).parse("杭州玩三天，喜欢拍照，想轻松一点")

    assert result.interests == ["摄影"]
    assert "轻松" in result.preference_text
