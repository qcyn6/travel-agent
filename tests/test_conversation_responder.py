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
    assert "当前支持全国省级行政区和目录中的城市" in system_message
    assert "确认需求并生成计划" in system_message
    assert "右侧旅行需求卡" not in FakeAsyncClient.last_payload["messages"][-1]["content"]
