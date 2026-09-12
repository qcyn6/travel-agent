from dataclasses import dataclass
from inspect import isawaitable

from backend.conversation.responder import ConversationResponseError
from backend.destination import DestinationResolutionError, DestinationResolver
from backend.models.conversation import Conversation, ConversationMessage, RequirementDraft
from backend.requirements.parser import canonicalize_interests


class ConversationNotFoundError(LookupError):
    pass


class RequirementNotReadyError(ValueError):
    pass


@dataclass
class PreparedConversationTurn:
    row: object
    conversation: Conversation
    user_message: str
    requirement: RequirementDraft
    missing: list[str]
    destination_context: str | None
    recent_messages: list[dict[str, str]]
    destination_error: str | None = None


class ConversationService:
    def __init__(self, *, repository, parser, resolver: DestinationResolver, responder):
        self.repository = repository
        self.parser = parser
        self.resolver = resolver
        self.responder = responder

    def create(self) -> Conversation:
        return self._to_model(self.repository.create())

    def get(self, conversation_id: str) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        return self._to_model(row)

    async def handle_message(self, conversation_id: str, message: str) -> tuple[Conversation, str, list[str]]:
        prepared = await self.prepare_message(conversation_id, message)
        if prepared.destination_error:
            reply = (
                f"{prepared.destination_error}。当前 MVP 支持浙江省、云南省和陕西省，你可以从这些地区中选择。"
            )
        elif not (prepared.requirement.province or prepared.requirement.cities):
            reply = self._fallback_reply(
                prepared.requirement,
                prepared.missing,
                prepared.destination_context,
            )
        else:
            try:
                reply = await self.responder.respond(
                    user_message=prepared.user_message,
                    requirement=prepared.requirement,
                    missing_fields=prepared.missing,
                    destination_context=prepared.destination_context,
                    recent_messages=prepared.recent_messages,
                )
            except ConversationResponseError:
                reply = self._fallback_reply(
                    prepared.requirement,
                    prepared.missing,
                    prepared.destination_context,
                )
        conversation = self.finish_message(prepared, reply)
        return conversation, reply, self.suggestions_for(prepared.missing)

    async def prepare_message(self, conversation_id: str, message: str) -> PreparedConversationTurn:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        clean_message = message.strip()
        self.repository.add_message(row, role="user", content=clean_message)

        current = RequirementDraft.model_validate(row.requirement_draft or {})
        parsed_value = self.parser.parse(message)
        parsed = await parsed_value if isawaitable(parsed_value) else parsed_value
        parsed_draft = RequirementDraft.model_validate(parsed.model_dump())
        merged = self._merge(current, parsed_draft)
        destination_error = None
        if merged.province or merged.cities:
            try:
                merged = RequirementDraft.model_validate(self.resolver.resolve(merged).requirement)
            except DestinationResolutionError as exc:
                destination_error = str(exc)
        self.repository.save_requirement(row, merged, confirmed=False)

        missing = self.missing_fields(merged)
        context = self._destination_context(merged)
        recent = [
            {"role": item.role, "content": item.content}
            for item in row.messages[-8:]
            if item.role in {"user", "assistant"}
        ]
        recent.append({"role": "user", "content": clean_message})
        return PreparedConversationTurn(
            row=row,
            conversation=self._to_model(self.repository.get(conversation_id, with_messages=True)),
            user_message=clean_message,
            requirement=merged,
            missing=missing,
            destination_context=context,
            recent_messages=recent,
            destination_error=destination_error,
        )

    def finish_message(self, prepared: PreparedConversationTurn, reply: str) -> Conversation:
        self.repository.add_message(prepared.row, role="assistant", content=reply)
        refreshed = self.repository.get(prepared.conversation.id, with_messages=True)
        return self._to_model(refreshed)

    def update_requirement(self, conversation_id: str, changes: dict) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        current = RequirementDraft.model_validate(row.requirement_draft or {})
        updated = current.model_copy(update=changes)
        updated = RequirementDraft.model_validate(updated)
        if updated.province or updated.cities:
            updated = RequirementDraft.model_validate(self.resolver.resolve(updated).requirement)
        self.repository.save_requirement(row, updated, confirmed=False)
        return self._to_model(self.repository.get(conversation_id, with_messages=True))

    def confirm(self, conversation_id: str) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        requirement = RequirementDraft.model_validate(row.requirement_draft or {})
        missing = self.missing_fields(requirement)
        if missing:
            labels = {"destination": "目的地", "days": "旅行天数", "preferences": "兴趣或体验偏好"}
            raise RequirementNotReadyError("还需要补充：" + "、".join(labels[item] for item in missing))
        self.repository.save_requirement(row, requirement, confirmed=True)
        return self._to_model(self.repository.get(conversation_id, with_messages=True))

    @staticmethod
    def missing_fields(requirement: RequirementDraft) -> list[str]:
        missing = []
        if not requirement.province and not requirement.cities:
            missing.append("destination")
        if requirement.days is None:
            missing.append("days")
        if not requirement.interests and not requirement.preference_text:
            missing.append("preferences")
        return missing

    @staticmethod
    def suggestions_for(missing: list[str]) -> list[str]:
        if not missing:
            return ["查看并确认旅行需求", "我还想补充"]
        if missing[0] == "destination":
            return ["云南省", "浙江省", "陕西省"]
        if missing[0] == "days":
            return ["3天", "5天", "7天", "还没确定"]
        return ["自然风光", "历史文化", "古城", "摄影", "美食", "休闲慢游"]

    @staticmethod
    def _merge(current: RequirementDraft, parsed: RequirementDraft) -> RequirementDraft:
        interests = canonicalize_interests([*current.interests, *parsed.interests])
        scope = parsed.travel_scope
        if scope == "auto" and current.travel_scope != "auto":
            scope = current.travel_scope
        return RequirementDraft(
            province=parsed.province or current.province,
            cities=parsed.cities or current.cities,
            days=parsed.days or current.days,
            interests=interests,
            preference_text=parsed.preference_text or current.preference_text,
            travel_scope=scope,
        )

    def _destination_context(self, requirement: RequirementDraft) -> str | None:
        if requirement.cities:
            city = self.resolver.repository.find_city(requirement.cities[0])
            return city.description if city else None
        if requirement.province:
            province = self.resolver.repository.find_province(requirement.province)
            return province.description if province else None
        return None

    @staticmethod
    def _fallback_reply(requirement: RequirementDraft, missing: list[str], context: str | None) -> str:
        intro = f"{context[:70]}。" if context else "我会逐步帮你整理这次旅行。"
        if not missing:
            return "你的核心旅行需求已经比较清晰了。请查看右侧需求卡，确认后我就开始生成行程。"
        questions = {
            "destination": "你想去哪个省份或城市？当前演示支持浙江省、云南省和陕西省。",
            "days": "这次大概准备玩几天？",
            "preferences": "你更喜欢自然、历史文化、美食、摄影，还是轻松慢游？",
        }
        return f"{intro}{questions[missing[0]]}"

    @staticmethod
    def _to_model(row) -> Conversation:
        requirement = RequirementDraft.model_validate(row.requirement_draft or {})
        messages = [ConversationMessage.model_validate(item) for item in getattr(row, "messages", [])]
        return Conversation(
            id=row.id,
            title=row.title,
            status=row.status,
            requirement=requirement,
            requirement_ready=requirement.is_ready(),
            requirement_confirmed=row.requirement_confirmed,
            messages=messages,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
