from datetime import date, datetime
from types import SimpleNamespace

import pytest

from backend.conversation.responder import ConversationResponseError
from backend.conversation.service import ConversationService, RequirementNotReadyError
from backend.destination import DestinationResolver
from backend.models.conversation import RequirementDraft
from backend.requirements.parser import RequirementParser


class FakeConversationRepository:
    def __init__(self):
        self.row = None
        self.next_id = 1

    def create(self):
        now = datetime.now()
        self.row = SimpleNamespace(
            id="conversation-1",
            title="新的旅行计划",
            status="draft",
            requirement_draft=RequirementDraft().model_dump(),
            requirement_confirmed=False,
            messages=[],
            created_at=now,
            updated_at=now,
        )
        return self.row

    def get(self, conversation_id, *, with_messages=False):
        return self.row if self.row and self.row.id == conversation_id else None

    def save_requirement(self, row, requirement, *, confirmed=None):
        row.requirement_draft = requirement.model_dump()
        if confirmed is not None:
            row.requirement_confirmed = confirmed
            row.status = "confirmed" if confirmed else "draft"

    def add_message(self, row, *, role, content):
        message = SimpleNamespace(
            id=self.next_id,
            sequence_number=self.next_id,
            role=role,
            content=content,
            created_at=datetime.now(),
        )
        self.next_id += 1
        row.messages.append(message)
        return message


class FailingResponder:
    async def respond(self, **kwargs):
        raise ConversationResponseError("offline")


@pytest.mark.asyncio
async def test_conversation_merges_multiple_turns_and_keeps_memory(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    first, _, first_suggestions = await service.handle_message(conversation.id, "我想去云南")
    second, reply, _ = await service.handle_message(conversation.id, "玩五天，喜欢自然和古城，想轻松一点")

    assert first.requirement.province == "云南省"
    assert first_suggestions[0] == "3天"
    assert second.requirement.province == "云南省"
    assert second.requirement.days == 5
    assert second.requirement.interests == ["自然", "古城"]
    assert second.requirement.preference_text is not None
    assert second.requirement_ready is True
    assert "我帮你把这次旅行需求整理好了" in reply
    assert "确认需求并生成计划" in reply
    assert "确认" in reply
    assert [message.role for message in second.messages] == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_conversation_switching_province_clears_previous_city(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    first, _, _ = await service.handle_message(conversation.id, "我想去杭州玩三天")
    second, _, _ = await service.handle_message(conversation.id, "改去云南玩五天")

    assert first.requirement.cities == ["杭州市"]
    assert second.requirement.province == "云南省"
    assert second.requirement.cities == []
    assert second.requirement.days == 5


def test_confirmation_requires_core_fields(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    with pytest.raises(RequirementNotReadyError, match="旅行天数"):
        service.confirm(conversation.id)


def test_manual_requirement_update_can_be_confirmed(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()
    updated = service.update_requirement(
        conversation.id,
        {
            "province": "浙江",
            "days": 3,
            "interests": ["历史文化", "摄影"],
            "travel_scope": "single_city",
        },
    )
    confirmed = service.confirm(conversation.id)

    assert updated.requirement.province == "浙江省"
    assert confirmed.requirement_confirmed is True
    assert confirmed.status == "confirmed"


@pytest.mark.asyncio
async def test_complete_requirement_summary_includes_resolved_relative_date(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    _, reply, suggestions = await service.handle_message(
        conversation.id,
        "明天去杭州玩三天，喜欢自然",
    )

    assert f"出发日期：{date.today().year}年" in reply
    assert suggestions[0] == "确认需求并生成计划"
