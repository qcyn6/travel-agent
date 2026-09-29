from datetime import date
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from backend.conversation.langchain_general import (
    LangChainGeneralAgent,
    LangChainGeneralResponder,
    LangChainGeneralTravelTools,
    LangChainGuideRetriever,
)
from backend.models.conversation import RequirementDraft
from backend.models.general_chat import GeneralIntentDecision
from backend.rag.knowledge_service import AdaptiveKnowledgeContext


class FakeChain:
    async def astream(self, values):
        assert values["history"]
        assert "用户当前问题：介绍云南" in values["prompt"]
        yield AIMessageChunk(content="云南有丰富的自然风光")
        yield AIMessageChunk(content="和多民族文化。")


@pytest.mark.asyncio
async def test_general_responder_streams_from_langchain_chain():
    responder = LangChainGeneralResponder(
        None,
        base_url="https://example.test/v1",
        model="test-model",
        chain=FakeChain(),
    )

    chunks = [
        chunk
        async for chunk in responder.stream(
            user_message="介绍云南",
            requirement=RequirementDraft(),
            missing_fields=[],
            destination_context=None,
            recent_messages=[{"role": "user", "content": "我想了解云南"}],
        )
    ]

    assert chunks == ["云南有丰富的自然风光", "和多民族文化。"]


@pytest.mark.asyncio
async def test_general_travel_tools_use_structured_langchain_tools():
    class Service:
        last_card = None

        async def query_route(self, decision):
            self.last_card = {"type": "route"}
            return f"{decision.origin}到{decision.destination}"

        async def query_weather(self, decision):
            self.last_card = {"type": "weather"}
            return f"{decision.city}天气"

    adapter = LangChainGeneralTravelTools(Service())
    reply = await adapter.execute(
        GeneralIntentDecision(
            intent="route",
            origin="杭州东站",
            destination="西湖",
            route_mode="transit",
        )
    )

    result = json.loads(reply)
    assert result["status"] == "ok"
    assert result["message"] == "杭州东站到西湖"
    assert adapter.last_card == {"type": "route"}
    assert {tool.name for tool in adapter.tools} == {
        "amap_route",
        "qweather_forecast",
        "amap_nearby_search",
    }


@pytest.mark.asyncio
async def test_general_travel_tools_group_multiple_weather_dates_into_one_card():
    class Service:
        last_card = None

        def __init__(self):
            self.today_provider = lambda: date(2026, 9, 22)

        async def query_weather(self, decision):
            weather = [
                {
                    "city": decision.city,
                    "date": target_date.isoformat(),
                    "condition": "多云",
                    "temperature_min": 24,
                    "temperature_max": 32,
                    "precipitation_probability": 0.03,
                    "wind_level": "2",
                    "status": "available",
                    "source": "qweather",
                }
                for target_date in decision.forecast_dates
            ]
            self.last_card = {"type": "weather", "weather": weather}
            return f"{decision.city}天气"

    adapter = LangChainGeneralTravelTools(Service())
    adapter.begin_turn("福州市未来两天天气")
    reply = await adapter.weather_tool.ainvoke(
        {"city": "福州市", "dates": ["2026-09-23", "2026-09-24"]}
    )

    assert json.loads(reply)["status"] == "ok"
    assert adapter.last_card["type"] == "weather"
    assert [item["date"] for item in adapter.last_card["weather"]] == [
        "2026-09-23",
        "2026-09-24",
    ]


@pytest.mark.asyncio
async def test_general_travel_tools_excludes_today_from_future_weather_range():
    class Service:
        last_card = None

        def __init__(self):
            self.today_provider = lambda: date(2026, 9, 22)
            self.calls = []

        async def query_weather(self, decision):
            self.calls.extend(decision.forecast_dates)
            weather = [
                {
                    "city": decision.city,
                    "date": target_date.isoformat(),
                    "condition": "多云",
                    "temperature_min": 24,
                    "temperature_max": 32,
                    "precipitation_probability": 0.03,
                    "wind_level": "2",
                    "status": "available",
                    "source": "qweather",
                }
                for target_date in decision.forecast_dates
            ]
            self.last_card = {"type": "weather", "weather": weather}
            return f"{decision.city}{decision.target_date}天气"

    service = Service()
    adapter = LangChainGeneralTravelTools(service)
    adapter.begin_turn("福州未来三天天气")

    assert "dates" in adapter.weather_tool.args
    assert "target_date" not in adapter.weather_tool.args

    reply = await adapter.weather_tool.ainvoke(
        {
            "city": "福州市",
            "dates": ["2026-09-23", "2026-09-24", "2026-09-25"],
        }
    )

    assert json.loads(reply)["status"] == "ok"
    assert service.calls == [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
    assert [item["date"] for item in adapter.last_card["weather"]] == [
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
    ]


@pytest.mark.asyncio
async def test_langchain_guide_retriever_uses_async_retriever_contract():
    class Service:
        async def adaptive_context(self, query, limit=4):
            assert query == "高原旅行注意事项"
            assert limit == 4
            return AdaptiveKnowledgeContext(
                context="高原旅行要注意防晒和高原反应。",
                query=query,
                attempts=1,
                sufficient=True,
            )

    retriever = LangChainGuideRetriever(service=Service())
    documents = await retriever.ainvoke("高原旅行注意事项")

    assert documents[0].page_content == "高原旅行要注意防晒和高原反应。"
    assert documents[0].metadata["sufficient"] is True


@pytest.mark.asyncio
async def test_general_agent_streams_from_create_agent():
    agent = LangChainGeneralAgent(
        None,
        base_url="https://example.test/v1",
        model="test-model",
        llm=FakeMessagesListChatModel(responses=[AIMessage(content="云南很值得去。")]),
    )

    chunks = [
        chunk
        async for chunk in agent.stream(
            user_message="介绍云南",
            requirement=RequirementDraft(),
            missing_fields=[],
            destination_context=None,
            recent_messages=[],
            decision=GeneralIntentDecision(intent="knowledge"),
            retrieval_allowed=False,
        )
    ]

    assert "云南很值得去。" in "".join(chunks)
