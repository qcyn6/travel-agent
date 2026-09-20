import pytest

from backend.conversation.responder import DeepSeekConversationResponder
from backend.models.conversation import RequirementDraft


class FakeStreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"当前"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"支持浙江省。"}}]}'
        yield "data: [DONE]"


class FakeAsyncClient:
    last_payload = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url, *, headers, json):
        type(self).last_payload = json
        return FakeStreamResponse()


@pytest.mark.asyncio
async def test_deepseek_responder_consumes_upstream_stream(monkeypatch):
    monkeypatch.setattr("backend.conversation.responder.httpx.AsyncClient", FakeAsyncClient)
    responder = DeepSeekConversationResponder(
        "test-key",
        base_url="https://example.test/v1",
        model="deepseek-chat",
    )

    chunks = [
        chunk
        async for chunk in responder.stream(
            user_message="你好",
            requirement=RequirementDraft(),
            missing_fields=["destination"],
            destination_context=None,
            recent_messages=[],
        )
    ]

    assert chunks == ["当前", "支持浙江省。"]
    assert FakeAsyncClient.last_payload["stream"] is True
    system_message = FakeAsyncClient.last_payload["messages"][0]["content"]
    prompt = FakeAsyncClient.last_payload["messages"][-1]["content"]
    assert "规划范围是中国境内已经收录的省份和城市" in system_message
    assert "根据兴趣和偏好推荐" in system_message
    assert "确认生成计划" in system_message
    assert "景点间路线不需要用户指定" in system_message
    assert "不要询问用户如何到达目的地，也不要询问景点之间怎么移动" in prompt
    assert "景点间路线由地图工具根据实际位置自动推荐" in prompt
    assert "右侧旅行需求卡" not in FakeAsyncClient.last_payload["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_general_prompt_does_not_offer_planning_in_general_mode(monkeypatch):
    monkeypatch.setattr("backend.conversation.responder.httpx.AsyncClient", FakeAsyncClient)
    responder = DeepSeekConversationResponder(
        "test-key",
        base_url="https://example.test/v1",
        model="deepseek-chat",
    )

    reply = await responder.respond(
        user_message="你能做什么？",
        requirement=RequirementDraft(),
        missing_fields=[],
        destination_context=None,
        recent_messages=[],
        conversation_mode="general",
    )

    assert reply == "当前支持浙江省。"
    system_message = FakeAsyncClient.last_payload["messages"][0]["content"]
    prompt = FakeAsyncClient.last_payload["messages"][-1]["content"]
    assert "只能提示“请切换到规划模式”" in system_message
    assert "不得说“告诉我我来安排”" in system_message
    assert "不要继续追问任何规划信息" in prompt
    assert "不要承诺当前普通对话会生成行程" in prompt
