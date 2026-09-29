import pytest

from backend.models.requirement import TravelRequirement
from backend.requirements.langchain_agent import LangChainRequirementAgent


@pytest.mark.asyncio
async def test_langchain_requirement_agent_returns_existing_requirement_model():
    class FakeAgent:
        async def ainvoke(self, payload):
            assert "用户输入" in payload["messages"][0]["content"]
            return {
                "structured_response": TravelRequirement(
                    province="浙江省",
                    cities=["杭州市"],
                    province_preference_confirmed=True,
                    city_preference_confirmed=True,
                    days=1,
                    interests=["自然"],
                    preference_text="轻松",
                    travel_scope="single_city",
                    transport_mode="auto",
                )
            }

    result = await LangChainRequirementAgent(
        None,
        base_url="https://example.com",
        model="test-model",
        agent=FakeAgent(),
    ).parse("杭州玩一天")

    assert result.cities == ["杭州市"]
    assert result.transport_mode == "auto"


@pytest.mark.asyncio
async def test_langchain_requirement_agent_includes_full_conversation_context():
    captured = {}

    class FakeAgent:
        async def ainvoke(self, payload):
            captured["prompt"] = payload["messages"][0]["content"]
            return {
                "structured_response": TravelRequirement(
                    province="云南省",
                    cities=["大理市", "昆明市", "丽江市"],
                    city_preference_confirmed=True,
                    days=7,
                    interests=["自然"],
                    travel_scope="multi_city",
                    transport_mode="auto",
                )
            }

    result = await LangChainRequirementAgent(
        None,
        base_url="https://example.com",
        model="test-model",
        agent=FakeAgent(),
    ).parse(
        "等会，我突然想再加一个丽江",
        conversation_history=[
            {"role": "user", "content": "我想去云南"},
            {"role": "assistant", "content": "你准备玩几天？"},
            {"role": "user", "content": "7天，想去大理和昆明"},
        ],
    )

    assert result.cities == ["大理市", "昆明市", "丽江市"]
    assert "我想去云南" in captured["prompt"]
    assert "7天，想去大理和昆明" in captured["prompt"]
    assert "等会，我突然想再加一个丽江" in captured["prompt"]
