from dataclasses import dataclass
import logging
import re

from backend.conversation.general_repository import GeneralConversationRepository
from backend.conversation.general_intent import (
    GeneralIntentRouter,
)
from backend.conversation.service import ConversationNotFoundError
from backend.destination import DestinationResolutionError, DestinationResolver
from backend.models.conversation import GeneralConversation, RequirementDraft
from backend.models.general_chat import GeneralIntent, GeneralIntentDecision

logger = logging.getLogger(__name__)

_MEMORY_WINDOW_MESSAGES = 8
# Keep a small spillover window and summarize in batches so we do not spend an
# extra model call every time one new turn pushes a message out of the window.
_MEMORY_COMPACT_AFTER_MESSAGES = 16
_MEMORY_FALLBACK_MAX_MESSAGES = 32
_MEMORY_FALLBACK_MAX_CHARS = 16_000


def _history_start(messages: list[dict], start: int) -> int:
    """Keep the retained history aligned to a user/assistant turn boundary."""

    while start > 0 and start < len(messages) and messages[start].get("role") == "assistant":
        start -= 1
    return start


def _history_text(messages: list[dict]) -> list[dict[str, str]]:
    return [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in messages
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]


def _bounded_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Bound the raw fallback context if memory summarization is unavailable."""

    result: list[dict[str, str]] = []
    total_chars = 0
    for item in reversed(messages[-_MEMORY_FALLBACK_MAX_MESSAGES:]):
        content = item["content"]
        remaining = _MEMORY_FALLBACK_MAX_CHARS - total_chars
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[-remaining:]
        result.append({"role": item["role"], "content": content})
        total_chars += len(content)
    return list(reversed(result))


@dataclass
class PreparedGeneralConversationTurn:
    row: object
    conversation: GeneralConversation
    user_message: str
    recent_messages: list[dict[str, str]]
    destination_context: str | None
    intent: GeneralIntent
    memory_summary: str | None = None
    memory_summary_messages: list[dict[str, str]] | None = None
    memory_summary_target: int | None = None
    memory_base_through: int = 0
    current_user_sequence: int = 0
    decision: GeneralIntentDecision | None = None
    retrieval_query: str | None = None
    retrieval_attempts: int = 0
    retrieval_sufficient: bool | None = None
    boundary_reply: str | None = None
    quick_reply: str | None = None
    tool_reply: str | None = None
    tool_card: dict[str, object] | None = None
    suggestions: list[str] | None = None


class GeneralConversationService:
    """Ordinary chat service with no access to planning requirements."""

    def __init__(
        self,
        *,
        repository: GeneralConversationRepository,
        resolver: DestinationResolver | None = None,
        knowledge_retriever=None,
        knowledge_repository=None,
        travel_tools=None,
        intent_router: GeneralIntentRouter | None = None,
    ):
        self.repository = repository
        self.resolver = resolver
        self.knowledge_retriever = knowledge_retriever
        self.knowledge_repository = knowledge_repository
        self.travel_tools = travel_tools
        # Kept as an optional constructor argument for compatibility only.
        # Ordinary turns are no longer classified by this rule-based router.

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
        *,
        execute_actions: bool = True,
    ) -> PreparedGeneralConversationTurn:
        row = self.repository.get(conversation_id)
        if row is None:
            raise ConversationNotFoundError("当前普通对话不存在或已失效")
        clean_message = message.strip()
        stored_messages = list(row.messages or [])
        memory_base_through = min(
            max(0, int(row.memory_summary_through or 0)),
            len(stored_messages),
        )
        memory_summary = row.memory_summary or None
        uncompacted_history = _history_text(stored_messages[memory_base_through:])
        recent = _bounded_history(uncompacted_history)
        summary_target = None
        summary_messages = None
        if len(stored_messages) - memory_base_through > _MEMORY_COMPACT_AFTER_MESSAGES:
            summary_target = _history_start(
                stored_messages,
                max(0, len(stored_messages) - _MEMORY_WINDOW_MESSAGES),
            )
            if summary_target > memory_base_through:
                summary_messages = _history_text(
                    stored_messages[memory_base_through:summary_target]
                )
        # The ordinary-mode Agent decides whether to answer, ask a question,
        # or invoke one or more tools. This service only owns conversation-local
        # history and memory; it does not classify or pre-execute the turn.
        current_user_sequence = self.repository.add_message(
            row,
            role="user",
            content=clean_message,
        )
        refreshed = self.repository.get(conversation_id)
        return PreparedGeneralConversationTurn(
            row=refreshed,
            conversation=self.repository.to_model(refreshed),
            user_message=clean_message,
            recent_messages=recent,
            destination_context=None,
            intent="general",
            memory_summary=memory_summary,
            memory_summary_messages=summary_messages,
            memory_summary_target=summary_target,
            memory_base_through=memory_base_through,
            current_user_sequence=current_user_sequence,
            suggestions=["你能做什么？", "介绍一个目的地"],
        )

    async def refresh_memory(self, prepared: PreparedGeneralConversationTurn, summarizer) -> None:
        """Compact older ordinary-chat turns without making memory a dependency."""

        target = prepared.memory_summary_target
        messages = prepared.memory_summary_messages or []
        if target is None or not messages or not callable(summarizer):
            return
        try:
            summary = await summarizer(
                previous_summary=prepared.memory_summary,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - memory failure must not block chat
            logger.warning("普通对话记忆摘要失败，继续使用滑动窗口：%s", type(exc).__name__)
            return
        if summary is None:
            return
        try:
            saved = self.repository.update_memory_summary(
                prepared.conversation.id,
                summary=summary,
                summarized_through=target,
            )
        except Exception as exc:  # noqa: BLE001 - storage failure must not block chat
            logger.warning("保存普通对话记忆失败，继续使用滑动窗口：%s", type(exc).__name__)
            return
        if saved is None:
            return
        prepared.memory_summary, prepared.memory_base_through = saved
        try:
            latest = self.repository.get(prepared.conversation.id)
        except Exception as exc:  # noqa: BLE001 - storage failure must not block chat
            logger.warning("读取普通对话记忆窗口失败：%s", type(exc).__name__)
            return
        if latest is not None:
            # Do not duplicate the current user message: the Agent adds it as
            # the final HumanMessage after this retained history.
            history_end = max(0, prepared.current_user_sequence - 1)
            uncompacted = list(latest.messages or [])[:history_end]
            uncompacted = uncompacted[prepared.memory_base_through:]
            prepared.recent_messages = _bounded_history(_history_text(uncompacted))
        prepared.memory_summary_messages = None
        prepared.memory_summary_target = None

    async def _knowledge_context(
        self,
        message: str,
        destination_names: list[str],
    ) -> tuple[str | None, str, int, bool]:
        chunks: list[str] = []
        city_names = [
            name
            for name in destination_names
            if not name.endswith(("省", "自治区", "特别行政区"))
        ]
        if self.knowledge_repository is not None:
            try:
                documents = self.knowledge_repository.search_documents(
                    message,
                    city_names=city_names,
                    limit=5,
                )
                attraction_context = self._format_attraction_context(documents)
                if attraction_context:
                    chunks.append(attraction_context)
            except Exception as exc:  # noqa: BLE001 - knowledge failure must not block chat
                logger.warning("普通对话景点知识检索失败，继续使用其他知识源：%s", type(exc).__name__)
        if self.knowledge_retriever is None:
            context = "\n\n".join(chunks) if chunks else None
            return context, message, 0, bool(context)
        try:
            adaptive_context = getattr(self.knowledge_retriever, "adaptive_context", None)
            if callable(adaptive_context):
                result = await adaptive_context(
                    message,
                    destination_names=destination_names,
                )
                if result.context:
                    chunks.append(result.context)
                context = "\n\n".join(chunks) if chunks else None
                return (
                    context,
                    result.query,
                    result.attempts,
                    bool(result.sufficient and context),
                )
            context = await self.knowledge_retriever.context(
                message,
                destination_names=destination_names,
            )
            if context:
                chunks.append(context)
        except Exception as exc:  # noqa: BLE001 - knowledge failure must not block chat
            logger.warning("普通对话知识库检索失败，继续使用模型：%s", type(exc).__name__)
        context = "\n\n".join(chunks) if chunks else None
        return context, message, 1, bool(context)

    async def _tool_reply(self, decision: GeneralIntentDecision) -> tuple[str, dict[str, object] | None]:
        if self.travel_tools is not None:
            try:
                reply = await self.travel_tools.execute(decision)
                if reply:
                    return reply, getattr(self.travel_tools, "last_card", None)
            except Exception as exc:  # noqa: BLE001 - tool failure must not break SSE
                logger.warning("普通模式工具调用失败：%s", type(exc).__name__)
        if decision.includes("route"):
            if "origin" in decision.missing_fields:
                return "要查询这条路线，我还需要出发地点。请告诉我你从哪里出发。", None
            if "destination" in decision.missing_fields:
                return "要查询这条路线，我还需要目的地。请告诉我你想去哪里。", None
            return "路线服务暂时不可用，我不能给出未经查询的路线耗时。请稍后重试。", None
        if decision.includes("nearby"):
            if "nearby_center" in decision.missing_fields and "nearby_category" in decision.missing_fields:
                return (
                    "请告诉我一个具体地点和想找的服务，例如“杭州东站附近的厕所”。"
                    "我目前无法获取你手机的实时位置。",
                    None,
                )
            if "nearby_center" in decision.missing_fields:
                return (
                    f"你想以哪个具体地点为中心找{decision.nearby_category or '附近服务'}？"
                    "可以告诉我景点、车站或商圈；我目前无法获取你手机的实时位置。",
                    None,
                )
            if "nearby_category" in decision.missing_fields:
                return (
                    f"你想在{decision.nearby_center}附近想找什么？"
                    "例如厕所、停车场、地铁站、药店或餐饮。",
                    None,
                )
            return "附近地点查询暂时不可用，请稍后重试。", None
        if "city" in decision.missing_fields:
            return "查询天气还需要查询天气的城市，请告诉我城市名称。", None
        return "天气服务暂时不可用，请稍后重试。", None

    @staticmethod
    def _format_attraction_context(documents) -> str | None:
        chunks = []
        for document in documents:
            facts = []
            if document.category:
                facts.append(f"分类：{document.category}")
            if document.interest_tags:
                facts.append(f"标签：{'、'.join(document.interest_tags)}")
            if document.description:
                facts.append(f"简介：{document.description.strip()}")
            detail = "；".join(facts) if facts else "数据库暂无更多介绍"
            chunks.append(f"【景点资料：{document.name}（{document.city}）】\n{detail}")
        return "\n\n".join(chunks) if chunks else None

    @staticmethod
    def _suggestions(decision: GeneralIntentDecision) -> list[str]:
        if decision.includes("weather") and "city" in decision.missing_fields:
            return ["杭州", "北京", "成都"]
        if decision.includes("nearby") and "nearby_category" in decision.missing_fields:
            return ["厕所", "停车场", "地铁站", "药店", "餐饮"]
        if decision.includes("route") or decision.intent == "planning":
            return []
        if decision.intent == "out_of_scope":
            return []
        return ["你能做什么？", "介绍一个目的地"]

    def finish_message(
        self,
        prepared: PreparedGeneralConversationTurn,
        reply: str,
        *,
        card: dict[str, object] | None = None,
    ) -> GeneralConversation:
        self.repository.add_message(
            prepared.row,
            role="assistant",
            content=reply,
            card=card,
        )
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
                "知道",
                "吗",
                "是什么",
                "什么是",
                "什么意思",
                "了解一下",
                "听说",
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
            "conversation_summary": prepared.memory_summary,
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
        return "当然可以。我们可以聊目的地、风景、人文、美食和旅行灵感；想制定完整行程时，再切到规划模式就好。"

    @staticmethod
    def quick_reply(message: str) -> str | None:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        if any(marker in normalized for marker in ("你好", "嗨", "您好")):
            return "你好，我是山。想聊哪个目的地，或者最近想去哪儿？风景、人文、美食和旅行灵感都可以。"
        if any(
            marker in normalized
            for marker in ("你是谁", "你能做什么", "你会做什么", "介绍一下你", "你有什么能力", "能帮我什么")
        ):
            return "我可以陪你了解目的地、聊风景人文和美食，也能帮你查路线、看天气。想制定完整行程时，再切到规划模式就好。"
        if any(marker in normalized for marker in ("谢谢", "感谢")):
            return "不客气，想到什么就聊什么。"
        return None


__all__ = ["GeneralConversationService", "PreparedGeneralConversationTurn"]
