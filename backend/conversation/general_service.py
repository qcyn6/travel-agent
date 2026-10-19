from dataclasses import dataclass
import logging
import re

from backend.conversation.general_repository import GeneralConversationRepository
from backend.conversation.service import ConversationNotFoundError
from backend.models.conversation import GeneralConversation, RequirementDraft
from backend.models.general_chat import GeneralIntent

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
    tool_card: dict[str, object] | None = None
    suggestions: list[str] | None = None


class GeneralConversationService:
    """Ordinary chat service with no access to planning requirements."""

    def __init__(
        self,
        *,
        repository: GeneralConversationRepository,
        knowledge_retriever=None,
        travel_tools=None,
    ):
        self.repository = repository
        self.knowledge_retriever = knowledge_retriever
        self.travel_tools = travel_tools

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
