from dataclasses import dataclass
import logging
import re

from backend.conversation.general_repository import GeneralConversationRepository
from backend.conversation.service import ConversationNotFoundError
from backend.destination import DestinationResolutionError, DestinationResolver
from backend.models.conversation import GeneralConversation, RequirementDraft
from backend.requirements.parser import RequirementParser

logger = logging.getLogger(__name__)


@dataclass
class PreparedGeneralConversationTurn:
    row: object
    conversation: GeneralConversation
    user_message: str
    recent_messages: list[dict[str, str]]
    destination_context: str | None
    boundary_reply: str | None = None
    quick_reply: str | None = None


class GeneralConversationService:
    """Ordinary chat service with no access to planning requirements."""

    def __init__(
        self,
        *,
        repository: GeneralConversationRepository,
        resolver: DestinationResolver | None = None,
        knowledge_retriever=None,
    ):
        self.repository = repository
        self.parser = RequirementParser()
        self.resolver = resolver
        self.knowledge_retriever = knowledge_retriever

    def create(self) -> GeneralConversation:
        return self.repository.to_model(self.repository.create())

    def get(self, conversation_id: str) -> GeneralConversation:
        row = self.repository.get(conversation_id)
        if row is None:
            raise ConversationNotFoundError("当前普通对话不存在或已失效")
        return self.repository.to_model(row)

    async def prepare_message(
        self,
        conversation_id: str,
        message: str,
    ) -> PreparedGeneralConversationTurn:
        row = self.repository.get(conversation_id)
        if row is None:
            raise ConversationNotFoundError("当前普通对话不存在或已失效")
        clean_message = message.strip()
        recent = [
            {"role": item["role"], "content": item["content"]}
            for item in (row.messages or [])[-8:]
        ]
        # Ordinary chat should not reject a casual sentence merely because it
        # contains an out-of-scope trip length. It only needs enough parsing to
        # detect whether the user is trying to start planning; the planning
        # endpoint performs the strict 1～7 day validation.
        parsed = self.parser.parse(clean_message, strict_days=False, strict_dates=False)
        self.repository.add_message(row, role="user", content=clean_message)
        boundary_reply = (
            "可以，具体行程需要在规划模式完成。请先切换到规划模式，切换后我会从目的地开始收集信息。"
            if self._has_planning_signal(clean_message, parsed)
            else None
        )
        quick_reply = None if boundary_reply else self.quick_reply(clean_message)
        context = None
        if boundary_reply is None and quick_reply is None and self.knowledge_retriever is not None:
            destination_names = [
                value
                for value in [parsed.province, *(parsed.cities or [])]
                if value
            ]
            if destination_names and self.resolver is not None:
                try:
                    resolved = self.resolver.resolve(
                        RequirementDraft.model_validate(parsed.model_dump())
                    ).requirement
                    destination_names = [
                        value
                        for value in [resolved.province, *(resolved.cities or [])]
                        if value
                    ]
                except DestinationResolutionError:
                    pass
            context = await self._knowledge_context(clean_message, destination_names)
        elif boundary_reply is None and self.resolver is not None and (parsed.province or parsed.cities):
            try:
                resolved = self.resolver.resolve(RequirementDraft.model_validate(parsed.model_dump())).requirement
                context = self._destination_context(RequirementDraft.model_validate(resolved.model_dump()))
            except DestinationResolutionError:
                context = None
        refreshed = self.repository.get(conversation_id)
        return PreparedGeneralConversationTurn(
            row=refreshed,
            conversation=self.repository.to_model(refreshed),
            user_message=clean_message,
            recent_messages=recent,
            destination_context=context,
            boundary_reply=boundary_reply,
            quick_reply=quick_reply,
        )

    async def _knowledge_context(
        self,
        message: str,
        destination_names: list[str],
    ) -> str | None:
        try:
            return await self.knowledge_retriever.context(
                message,
                destination_names=destination_names,
            )
        except Exception as exc:  # noqa: BLE001 - knowledge failure must not block chat
            logger.warning("普通对话知识库检索失败，继续使用模型：%s", type(exc).__name__)
            return None

    def finish_message(self, prepared: PreparedGeneralConversationTurn, reply: str) -> GeneralConversation:
        self.repository.add_message(prepared.row, role="assistant", content=reply)
        refreshed = self.repository.get(prepared.conversation.id)
        return self.repository.to_model(refreshed)

    @staticmethod
    def _has_planning_signal(message: str, parsed) -> bool:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip()).lower()
        markers = (
            "想去",
            "要去",
            "准备去",
            "打算去",
            "计划去",
            "去玩",
            "规划",
            "安排",
            "出发",
        )
        direct_trip = re.match(
            r"^(?:明天|后天|大后天|下周[一二三四五六日天]|周[一二三四五六日天])?去",
            normalized,
        )
        return bool(
            direct_trip
            or any(marker in normalized for marker in markers)
            or GeneralConversationService._has_day_expression(normalized)
            or parsed.days is not None
            or parsed.start_date is not None
            or (
                (parsed.interests or parsed.preference_text)
                and not GeneralConversationService._looks_like_knowledge_question(normalized)
            )
        )

    @staticmethod
    def _has_day_expression(normalized: str) -> bool:
        return bool(
            re.search(
                r"(?:\d+|[零〇一二两三四五六七八九十百千万亿]+)\s*(?:天|日)(?:游)?",
                normalized,
            )
        )

    @staticmethod
    def _looks_like_knowledge_question(normalized: str) -> bool:
        """Do not mistake a destination knowledge question for trip planning."""

        return any(
            marker in normalized
            for marker in (
                "介绍",
                "有哪些",
                "有什么",
                "特色",
                "特点",
                "值得",
                "适合",
                "了解",
                "推荐",
                "风景",
                "美食",
                "历史文化",
                "自然风光",
                "人文",
                "好玩吗",
            )
        )

    def _destination_context(self, requirement: RequirementDraft) -> str | None:
        if self.resolver is None:
            return None
        if requirement.cities:
            city = self.resolver.repository.find_city(requirement.cities[0])
            return city.description if city else None
        if requirement.province:
            province = self.resolver.repository.find_province(requirement.province)
            return province.description if province else None
        return None

    @staticmethod
    def responder_inputs(prepared: PreparedGeneralConversationTurn) -> dict[str, object]:
        return {
            "requirement": RequirementDraft(),
            "missing_fields": [],
            "destination_context": prepared.destination_context,
            "recent_messages": prepared.recent_messages,
            "changed_fields": [],
            "conversation_mode": "general",
        }

    @staticmethod
    def fallback_reply(message: str, context: str | None) -> str:
        quick_reply = GeneralConversationService.quick_reply(message)
        if quick_reply:
            return quick_reply
        if context:
            return f"{context.rstrip('，。；')}。你还可以继续了解这里的风景、人文、美食或当地体验。"
        return "可以聊聊目的地、风景、人文、美食或旅行灵感。需要制定具体行程时，请切换到规划模式。"

    @staticmethod
    def quick_reply(message: str) -> str | None:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        if any(marker in normalized for marker in ("你好", "嗨", "您好")):
            return "你好，我是“山”。普通模式可以聊目的地、风景、人文、美食和旅行灵感。需要制定具体行程时，请切换到规划模式。"
        if any(
            marker in normalized
            for marker in ("你是谁", "你能做什么", "你会做什么", "介绍一下你", "你有什么能力", "能帮我什么")
        ):
            return "我是“山”，可以陪你聊目的地、风景、人文、美食和旅行灵感，也能回答一般问题。需要制定具体行程时，请切换到规划模式。"
        if any(marker in normalized for marker in ("谢谢", "感谢")):
            return "不客气，想到什么就聊什么。"
        return None


__all__ = ["GeneralConversationService", "PreparedGeneralConversationTurn"]
