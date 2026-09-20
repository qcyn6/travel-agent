from datetime import date, datetime, timedelta
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


class MisleadingResponder:
    async def respond(self, **kwargs):
        return "5天也可以选一个城市深度游。"


class MisleadingReadyResponder:
    async def respond(self, **kwargs):
        return "省份和城市我都来安排，所有条件都齐了，请确认生成计划。"


@pytest.mark.asyncio
async def test_province_recommendation_is_a_real_conversation_step(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    _, first_reply, first_suggestions = await service.handle_message(
        conversation.id,
        "我不知道去哪里玩",
        mode="planning",
    )
    updated, recommendation_reply, recommendation_suggestions = await service.handle_message(
        conversation.id,
        "你可以推荐一个省份吗",
        mode="planning",
    )

    assert "你来推荐一个省份" in first_reply
    assert "你来推荐一个省份" in first_suggestions
    assert updated.requirement.province_preference_confirmed is True
    assert "这次准备玩几天" in recommendation_reply
    assert "哪个省份或城市" not in recommendation_reply
    assert recommendation_suggestions == ["1天", "2天", "3天", "4天", "5天", "6天", "7天"]


@pytest.mark.asyncio
async def test_pending_city_recommendation_cannot_show_ready_text(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=MisleadingReadyResponder(),
    )
    conversation = service.create()
    repository.row.requirement_draft = RequirementDraft(
        province_preference_confirmed=True,
        days=4,
        interests=["自然"],
        travel_scope="multi_city",
        start_date=date.today() + timedelta(days=1),
        daily_start_time="10:00",
        daily_end_time="23:00",
    ).model_dump()

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "好的",
        mode="planning",
    )

    assert updated.requirement_ready is False
    assert "有没有特别想去的城市" in reply
    assert "所有条件都齐了" not in reply
    assert suggestions == ["没有指定城市，你来推荐"]


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
    second, second_reply, _ = await service.handle_message(
        conversation.id,
        "玩三天，喜欢自然和古城，想轻松一点，明天出发，每天8点到21点，公共交通，安排一个城市",
    )
    third, reply, _ = await service.handle_message(
        conversation.id,
        "没有指定城市，你来推荐一个",
    )

    assert first.requirement.province == "云南省"
    assert first_suggestions[0] == "1天"
    assert second.requirement.province == "云南省"
    assert second.requirement.days == 3
    assert second.requirement.interests == ["自然", "古城"]
    assert second.requirement.preference_text is not None
    assert second.requirement_ready is False
    assert "有没有特别想去的城市" in second_reply
    assert third.requirement_ready is True
    assert "我已经抓住这次旅行的重点了" in reply
    assert "确认生成计划" in reply
    assert "确认" in reply
    assert [message.role for message in second.messages] == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_short_province_trip_recommends_single_city_then_confirms_city_preference(
    catalog_repository,
):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    _, scope_reply, scope_suggestions = await service.handle_message(
        conversation.id,
        "去云南玩三天，喜欢自然，明天出发，每天8点到21点",
        mode="planning",
    )

    assert "3天更适合安排一个城市深度游" in scope_reply
    assert "增加到4天以上" in scope_reply
    assert scope_suggestions == ["一个城市深度游", "增加到4天以上游多个城市"]

    updated, city_reply, city_suggestions = await service.handle_message(
        conversation.id,
        "一个城市深度游",
        mode="planning",
    )

    assert updated.requirement_ready is False
    assert "有没有特别想去的城市" in city_reply
    assert city_suggestions == ["没有指定城市，你来推荐"]


def test_five_day_scope_never_offers_single_city(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    requirement = RequirementDraft(province="云南省", days=5)

    assert service.suggestions_for(["travel_scope"], requirement) == ["同省多城市"]
    assert "必须安排同省多城市" in service._fallback_reply(requirement, ["travel_scope"], None)


def test_invalid_short_multi_city_scope_only_suggests_longer_trip(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )

    assert service.validation_suggestions(
        "1～3天只支持单城市规划；同省多城市至少需要4天",
        RequirementDraft(),
    ) == ["4天", "5天", "6天", "7天"]


@pytest.mark.asyncio
async def test_five_day_boundary_does_not_allow_responder_to_offer_single_city(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=MisleadingResponder(),
    )
    conversation = service.create()

    _, reply, _ = await service.handle_message(
        conversation.id,
        "去云南玩5天",
        mode="planning",
    )

    assert "同省多城市" in reply
    assert "一个城市深度游" not in reply
    assert "有没有特别想去的城市" in reply


@pytest.mark.asyncio
async def test_multi_city_requires_city_preference_before_preferences(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    _, city_reply, city_suggestions = await service.handle_message(
        conversation.id,
        "去云南玩5天",
        mode="planning",
    )

    assert "有没有特别想去的城市" in city_reply
    assert city_suggestions == ["没有指定城市，你来推荐"]

    updated, preference_reply, _ = await service.handle_message(
        conversation.id,
        "大理和丽江",
        mode="planning",
    )

    assert updated.requirement.cities == ["大理市", "丽江市"]
    assert updated.requirement.city_preference_confirmed is True
    assert "自然风光" in preference_reply


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
    second, _, _ = await service.handle_message(conversation.id, "改去云南玩三天")

    assert first.requirement.cities == ["杭州市"]
    assert second.requirement.province == "云南省"
    assert second.requirement.cities == []
    assert second.requirement.days == 3


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
            "city_preference_confirmed": True,
            "start_date": date.today() + timedelta(days=1),
            "daily_start_time": "08:00",
            "daily_end_time": "21:00",
            "transport_mode": "transit",
        },
    )
    confirmed = service.confirm(conversation.id)

    assert updated.requirement.province == "浙江省"
    assert confirmed.requirement_confirmed is True
    assert confirmed.status == "confirmed"


def test_confirmation_requires_explicit_city_scope_for_province_only_trip(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()
    service.update_requirement(
        conversation.id,
        {
            "province": "云南省",
            "days": 5,
            "interests": ["自然"],
            "travel_scope": "multi_city",
            "start_date": date.today() + timedelta(days=1),
            "daily_start_time": "08:00",
            "daily_end_time": "21:00",
            "transport_mode": "transit",
        },
    )

    with pytest.raises(RequirementNotReadyError, match="具体城市或城市推荐方式"):
        service.confirm(conversation.id)


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

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "明天去杭州玩三天，喜欢自然，每天8点到21点",
    )

    assert f"出发时间安排在{date.today().year}年" in reply
    assert "景点间路线由地图工具根据实际位置自动推荐" in reply
    assert "景点间交通方式" not in reply
    assert updated.requirement.transport_mode == "auto"
    assert suggestions[0] == "确认生成计划"


@pytest.mark.asyncio
async def test_incomplete_requirement_cannot_be_confirmed_or_generated(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "去杭州玩三天，喜欢自然",
        mode="planning",
    )

    assert updated.requirement_ready is False
    assert "哪天出发" in reply
    assert "之后补上" not in reply
    assert suggestions == ["明天出发", "后天出发", "下周一出发"]
    with pytest.raises(RequirementNotReadyError, match="出发日期"):
        service.confirm(conversation.id)


@pytest.mark.asyncio
async def test_more_details_prompt_does_not_reopen_complete_requirements(catalog_repository):
    repository = FakeConversationRepository()
    responder = FailingResponder()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=responder,
    )
    conversation = service.create()

    await service.handle_message(conversation.id, "去杭州玩三天，喜欢自然，明天出发，每天8点到21点，公共交通")
    updated, reply, suggestions = await service.handle_message(conversation.id, "我还想补充")

    assert updated.requirement_ready is True
    assert "所有出行条件都已经确定" in reply
    assert "之后补上" not in reply
    assert suggestions[0] == "确认生成计划"


@pytest.mark.asyncio
async def test_general_conversation_does_not_change_travel_profile_or_push_planning(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    await service.handle_message(conversation.id, "去杭州玩三天，喜欢自然")
    before = repository.row.requirement_draft.copy()
    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "你是谁",
        mode="general",
    )

    assert updated.requirement.model_dump() == before
    assert "AI 旅行助手" in reply
    assert suggestions == ["你能做什么？", "介绍一个目的地"]


@pytest.mark.asyncio
async def test_general_mode_blocks_planning_request_without_mutating_requirement(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "去杭州玩三天，喜欢自然，明天出发，每天8点到21点，公共交通",
        mode="general",
    )

    assert updated.requirement == RequirementDraft()
    assert repository.row.requirement_draft == RequirementDraft().model_dump()
    assert "普通模式" in reply
    assert "规划模式" in reply
    assert "确认生成计划" not in reply
    assert suggestions == ["你能做什么？", "介绍一个目的地"]


@pytest.mark.asyncio
async def test_planning_mode_blocks_general_question_without_mutating_requirement(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "介绍一下云南省",
        mode="planning",
    )

    assert updated.requirement == RequirementDraft()
    assert repository.row.requirement_draft == RequirementDraft().model_dump()
    assert "规划模式" in reply
    assert "普通模式" in reply
    assert "确认生成计划" not in reply
    assert suggestions[0] in {"北京", "上海", "四川省", "云南省", "浙江省"}


@pytest.mark.asyncio
async def test_destination_information_question_keeps_existing_destination(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    await service.handle_message(conversation.id, "去杭州玩三天，喜欢自然")
    updated, reply, _ = await service.handle_message(conversation.id, "介绍一下云南省")

    assert updated.requirement.cities == ["杭州市"]
    assert updated.requirement.days == 3
    assert "确认生成计划" not in reply


@pytest.mark.asyncio
async def test_explicit_conversation_mode_overrides_automatic_intent_detection(catalog_repository):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    general, _, _ = await service.handle_message(
        conversation.id,
        "去杭州玩三天，喜欢自然，明天出发，每天8点到21点，公共交通",
        mode="general",
    )
    planning, reply, _ = await service.handle_message(
        conversation.id,
        "去杭州玩三天，喜欢自然，明天出发，每天8点到21点，公共交通",
        mode="planning",
    )

    assert general.requirement.province is None
    assert planning.requirement.cities == ["杭州市"]
    assert planning.requirement_ready is True
    assert "确认生成计划" in reply


@pytest.mark.asyncio
async def test_planning_conversation_rejects_out_of_range_days_without_mutating_requirement(
    catalog_repository,
):
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        parser=RequirementParser(),
        resolver=DestinationResolver(catalog_repository),
        responder=FailingResponder(),
    )
    conversation = service.create()

    updated, reply, suggestions = await service.handle_message(
        conversation.id,
        "去杭州玩365天，喜欢自然",
        mode="planning",
    )

    assert "1～7天" in reply
    assert updated.requirement == RequirementDraft()
    assert repository.row.requirement_draft == RequirementDraft().model_dump()
    assert suggestions == ["1天", "2天", "3天", "4天", "5天", "6天", "7天"]
    assert [message.role for message in updated.messages] == ["user", "assistant"]
