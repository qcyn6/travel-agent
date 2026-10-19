from dataclasses import dataclass
from inspect import Parameter, isawaitable, signature
import re

from backend.conversation.responder import (
    ConversationResponseError,
    PlanningResponseDecision,
)
from backend.conversation.graph import PlanningConversationGraph
from backend.database.catalog import DESTINATION_SUGGESTIONS
from backend.destination import DestinationResolutionError, DestinationResolver
from backend.models.conversation import (
    Conversation,
    ConversationMessage,
    RequirementDraft,
    load_requirement_draft,
)
from backend.models.requirement import MAX_SINGLE_CITY_DAYS
from backend.requirements.dates import format_date_zh
from backend.requirements.parser import RequirementBoundaryError, canonicalize_interests
from backend.requirements.preferences import is_meaningful_preference_text
from backend.tag_matching import normalize_interest_tags


class ConversationNotFoundError(LookupError):
    pass


class RequirementNotReadyError(ValueError):
    pass


class RequirementConflictError(ValueError):
    pass


NO_EXTRA_PREFERENCE_TEXT = "没有额外体验偏好"


@dataclass
class PreparedConversationTurn:
    row: object
    conversation: Conversation
    user_message: str
    requirement: RequirementDraft
    missing: list[str]
    destination_context: str | None
    recent_messages: list[dict[str, str]]
    changed_fields: list[str]
    intent: str = "planning"
    was_ready: bool = False
    should_summarize: bool = False
    should_invite_more: bool = False
    destination_error: str | None = None
    mode_boundary: str | None = None
    validation_error: str | None = None
    confirmation_prepared: bool | None = None


class ConversationService:
    def __init__(self, *, repository, parser=None, resolver: DestinationResolver, responder=None, planning_agent=None):
        self.repository = repository
        self.parser = parser
        self.resolver = resolver
        self.responder = responder
        self.planning_agent = planning_agent
        # Compatibility for standalone parser/service consumers. Production
        # dialogue uses the single Agent, not the former two-model turn graph.
        self.conversation_graph = PlanningConversationGraph(self) if planning_agent is None else None

    def create(self) -> Conversation:
        return self._to_model(self.repository.create())

    def get(self, conversation_id: str) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        return self._to_model(row)

    async def handle_message(
        self,
        conversation_id: str,
        message: str,
        *,
        mode: str | None = None,
    ) -> tuple[Conversation, str, list[str]]:
        conversation, reply, suggestions, _ = await self.run_graph(
            conversation_id,
            message,
            mode=mode,
        )
        return conversation, reply, suggestions

    async def run_graph(
        self,
        conversation_id: str,
        message: str,
        *,
        mode: str | None = None,
    ) -> tuple[Conversation, str, list[str], PreparedConversationTurn]:
        """Run the dialogue Agent (or the legacy parser adapter for old callers)."""

        if self.planning_agent is not None:
            return await self._run_agent_turn(conversation_id, message, mode=mode)
        return await self.conversation_graph.run(
            conversation_id,
            message,
            mode=mode,
        )

    async def _run_agent_turn(self, conversation_id, message, *, mode=None):
        import logging
        from backend.conversation.planning_agent import PlanningDialogueTurn

        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        if mode not in (None, "planning"):
            raise ValueError("规划对话 Agent 仅处理规划模式")
        clean_message = message.strip()
        if not clean_message:
            raise ValueError("消息不能为空")
        current = load_requirement_draft(row.requirement_draft or {})
        history = [{"role": item.role, "content": item.content} for item in row.messages
                   if item.role in {"user", "assistant"}]
        self.repository.add_message(row, role="user", content=clean_message)
        turn = PlanningDialogueTurn(self, current)
        ready = False
        next_field = None
        answered = False
        try:
            decision = await self.planning_agent.run(turn, clean_message, history)
        except Exception as exc:
            # No raw provider exceptions or unvalidated staged edits reach the UI/DB.
            logging.getLogger(__name__).warning("Planning dialogue failed: %s", type(exc).__name__)
            from backend.observability import metrics
            metrics.inc("travel_agent_planning_dialogue_failures_total", {"reason": type(exc).__name__})
            reply = "本轮需求整理暂时未完成，原有需求已保留，请重新发送刚才的补充或修改。"
        else:
            if turn.draft != current:
                saver = getattr(self.repository, "save_agent_requirement", None)
                if saver is not None:
                    if not saver(row, current, turn.draft):
                        raise RequirementConflictError("需求已在其他页面更新，请刷新后再发送")
                else:
                    self.repository.save_requirement(row, turn.draft, confirmed=False)
            current = turn.draft
            reply = decision.reply
            answered = True
            ready = decision.action == "confirm" and turn.confirmation_prepared
            next_field = decision.next_field
            if ready:
                # Confirmation text is rendered from exactly the checked draft,
                # not from a potentially inconsistent model-written summary.
                reply = self.summary_reply(current)
        prepared = PreparedConversationTurn(
            row=row, conversation=self._to_model(row), user_message=clean_message,
            requirement=current, missing=self.missing_fields(current),
            destination_context=None, recent_messages=history[-8:], changed_fields=[],
            confirmation_prepared=ready,
        )
        conversation = self.finish_message(prepared, reply)
        if ready:
            options = ["确认生成计划"]
        elif next_field in prepared.missing:
            options = self.suggestions_for([next_field], current)
        elif not answered and prepared.missing:
            options = self.suggestions_for(prepared.missing[:1], current)
        else:
            options = []
        return conversation, reply, options, prepared

    async def planning_reply(self, prepared: PreparedConversationTurn) -> str:
        """Let the LLM guide a planning turn while preserving hard guards."""

        decision = await self.planning_decision(prepared)
        return decision.reply

    async def planning_decision(
        self,
        prepared: PreparedConversationTurn,
    ) -> PlanningResponseDecision:
        """Return the AI's structured planning action, with a safe fallback."""

        try:
            decide = getattr(self.responder, "decide_planning", None)
            if callable(decide):
                decision = await decide(
                    user_message=prepared.user_message,
                    requirement=prepared.requirement,
                    missing_fields=prepared.missing,
                    destination_context=prepared.destination_context,
                    recent_messages=prepared.recent_messages,
                    changed_fields=prepared.changed_fields,
                )
            else:
                reply = await self.responder.respond(
                    user_message=prepared.user_message,
                    requirement=prepared.requirement,
                    missing_fields=prepared.missing,
                    destination_context=prepared.destination_context,
                    recent_messages=prepared.recent_messages,
                    changed_fields=prepared.changed_fields,
                    conversation_mode="planning",
                )
                decision = PlanningResponseDecision(
                    action="confirm" if not prepared.missing else "ask",
                    next_field=self.question_field_for_reply(reply, prepared),
                    reply=reply,
                    ready_for_confirmation=not prepared.missing,
                )
        except (ConversationResponseError, ValueError, TypeError):
            return self._planning_fallback_decision(prepared)
        if self._planning_decision_violates_state(decision, prepared):
            return self._planning_fallback_decision(prepared)
        return decision

    def _planning_fallback_decision(
        self,
        prepared: PreparedConversationTurn,
    ) -> PlanningResponseDecision:
        reply = self._planning_fallback(prepared)
        return PlanningResponseDecision(
            action="confirm" if not prepared.missing else "ask",
            next_field=self.question_field_for_reply(reply, prepared),
            reply=reply,
            ready_for_confirmation=not prepared.missing,
        )

    def _planning_decision_violates_state(
        self,
        decision: PlanningResponseDecision,
        prepared: PreparedConversationTurn,
    ) -> bool:
        if prepared.missing:
            if decision.action != "ask" or decision.ready_for_confirmation:
                return True
            if decision.next_field not in prepared.missing:
                return True
            return self._planning_reply_violates_state(decision.reply, prepared)
        if decision.action not in {"confirm", "generate"}:
            return True
        if not decision.ready_for_confirmation and decision.action == "confirm":
            return True
        return self._planning_reply_violates_state(decision.reply, prepared)

    @staticmethod
    def question_field_for_reply(
        reply: str,
        prepared: PreparedConversationTurn,
    ) -> str | None:
        """Map the question actually asked by the assistant back to its UI field."""

        if not prepared.missing:
            return None
        compact = re.sub(r"\s+", "", reply)
        cues = (
            ("days", ("玩几天", "游玩几天", "旅行几天", "待几天", "多少天")),
            (
                "daily_time_window",
                ("几点开始", "几点结束", "每天游玩时间", "每日游玩时间", "时间段"),
            ),
            ("start_date", ("哪天出发", "何时出发", "什么时候出发", "出发日期")),
            (
                "preference_text",
                ("旅行节奏", "游玩节奏", "体验偏好", "旅行风格", "赶时间"),
            ),
            (
                "preferences",
                ("兴趣标签", "更喜欢什么", "喜欢哪类", "自然风光", "历史文化"),
            ),
            (
                "city_preference",
                ("想去的城市", "指定城市", "哪个城市", "哪些城市", "推荐城市"),
            ),
            ("destination", ("想去的省份", "哪个省份", "推荐一个省份", "目的地")),
        )
        for field, markers in cues:
            if field in prepared.missing and any(marker in compact for marker in markers):
                return field
        return prepared.missing[0]

    def suggestions_for_reply(
        self,
        prepared: PreparedConversationTurn,
        reply: str,
    ) -> list[str]:
        field = self.question_field_for_reply(reply, prepared)
        return self.suggestions_for(
            [field] if field else [],
            prepared.requirement,
            intent=prepared.intent,
        )

    def suggestions_for_decision(
        self,
        prepared: PreparedConversationTurn,
        decision: PlanningResponseDecision,
    ) -> list[str]:
        """Build UI options from the same structured field the AI selected."""

        if decision.action in {"confirm", "generate"} and not prepared.missing:
            return ["确认生成计划"]
        field = decision.next_field
        if field not in prepared.missing:
            return self.suggestions_for_reply(prepared, decision.reply)
        return self.suggestions_for(
            [field],
            prepared.requirement,
            intent=prepared.intent,
        )

    @staticmethod
    def _planning_fallback(prepared: PreparedConversationTurn) -> str:
        if not prepared.missing:
            return ConversationService.summary_reply(
                prepared.requirement,
                changed_fields=prepared.changed_fields,
                was_ready=prepared.was_ready,
            )
        return ConversationService._fallback_reply(
            prepared.requirement,
            prepared.missing,
            prepared.destination_context,
        )

    @staticmethod
    def _planning_reply_violates_state(
        reply: str,
        prepared: PreparedConversationTurn,
    ) -> bool:
        compact = re.sub(r"\s+", "", reply)
        if prepared.missing and any(
            marker in compact
            for marker in ("所有条件都齐", "全部条件都齐", "确认生成计划", "可以生成计划")
        ):
            return True
        if (
            prepared.requirement.days is not None
            and prepared.requirement.days > MAX_SINGLE_CITY_DAYS
            and any(marker in compact for marker in ("单城市", "一个城市深度游"))
        ):
            return True
        if not prepared.missing and "确认" not in compact:
            return True
        return False

    @staticmethod
    def should_explain_multi_city_boundary(prepared: PreparedConversationTurn) -> bool:
        """Keep the 4～7-day boundary deterministic at the conversation edge."""

        return bool(
            prepared.intent == "planning"
            and prepared.requirement.days is not None
            and prepared.requirement.days > MAX_SINGLE_CITY_DAYS
            and "days" in prepared.changed_fields
        )

    async def prepare_message(
        self,
        conversation_id: str,
        message: str,
        *,
        mode: str | None = None,
    ) -> PreparedConversationTurn:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        clean_message = message.strip()
        conversation_history = [
            {"role": item.role, "content": item.content}
            for item in row.messages
            if item.role in {"user", "assistant"}
        ]
        recent = conversation_history[-8:]
        self.repository.add_message(row, role="user", content=clean_message)

        current = load_requirement_draft(row.requirement_draft or {})
        try:
            parsed_value = self._parse_requirement(
                message,
                conversation_history=conversation_history,
            )
            parsed = await parsed_value if isawaitable(parsed_value) else parsed_value
            parsed_draft = RequirementDraft.model_validate(parsed.model_dump())
            normalized_message = re.sub(r"[，。！？、\s]", "", clean_message)
            if (
                mode == "planning"
                and self.missing_fields(current)[:1] == ["destination"]
                and normalized_message
                in {"没有", "没有想好", "不知道", "没想好", "你推荐", "你来推荐"}
            ):
                # In the province question, a short “没有” means there is no
                # preferred province and the user authorizes recommendation.
                parsed_draft = parsed_draft.model_copy(
                    update={"province_preference_confirmed": True}
                )
            explicit_city_recommendation = (
                "城市" in normalized_message
                and self.confirms_city_recommendation(clean_message)
            )
            if (
                mode == "planning"
                and self.confirms_city_recommendation(clean_message)
                and (
                    self.missing_fields(current)[:1] == ["city_preference"]
                    or explicit_city_recommendation
                )
            ):
                # The current question supplies the meaning of a short answer
                # such as “你来推荐”.  Prefer this deterministic turn context
                # over an LLM that may mistake it for a province recommendation
                # and otherwise cause the same city question to repeat.
                parsed_draft = parsed_draft.model_copy(
                    update={
                        "city_preference_confirmed": True,
                        "province_preference_confirmed": self.confirms_province_recommendation(
                            clean_message
                        ),
                    }
                )
            if (
                mode == "planning"
                and self.missing_fields(current)[:1] == ["preference_text"]
            ):
                parsed_other_requirement = bool(
                    parsed_draft.province
                    or parsed_draft.cities
                    or parsed_draft.days is not None
                    or parsed_draft.interests
                    or parsed_draft.start_date is not None
                    or parsed_draft.daily_start_time is not None
                    or parsed_draft.daily_end_time is not None
                    or parsed_draft.travel_scope != "auto"
                )
                explicit_preference = is_meaningful_preference_text(
                    parsed_draft.preference_text,
                    current.interests,
                )
                parsed_draft = parsed_draft.model_copy(
                    update={
                        "preference_text": (
                            clean_message
                            if is_meaningful_preference_text(
                                clean_message,
                                current.interests,
                            )
                            and (explicit_preference or not parsed_other_requirement)
                            else None
                        )
                    }
                )
        except (RequirementBoundaryError, ValueError) as exc:
            return PreparedConversationTurn(
                row=row,
                conversation=self._to_model(self.repository.get(conversation_id, with_messages=True)),
                user_message=clean_message,
                requirement=current,
                missing=self.missing_fields(current),
                destination_context=self._destination_context(current),
                recent_messages=recent,
                changed_fields=[],
                intent=mode if mode in {"general", "planning"} else "planning",
                was_ready=current.is_ready(),
                validation_error=self._validation_reply(mode, str(exc)),
            )
        more_details_prompt = self.is_more_details_prompt(clean_message)
        detected_intent = self.detect_intent(clean_message, parsed_draft, current)
        intent = mode if mode in {"general", "planning"} else (
            "planning" if more_details_prompt and current.is_ready() else detected_intent
        )
        mode_boundary = self.mode_boundary_reply(mode, clean_message, parsed_draft)
        if intent == "general" or mode_boundary:
            merged = current
        else:
            try:
                merged = self._merge(current, parsed_draft, message=clean_message)
                if explicit_city_recommendation:
                    merged = merged.model_copy(
                        update={"city_preference_confirmed": True}
                    )
            except ValueError as exc:
                return PreparedConversationTurn(
                    row=row,
                    conversation=self._to_model(self.repository.get(conversation_id, with_messages=True)),
                    user_message=clean_message,
                    requirement=current,
                    missing=self.missing_fields(current),
                    destination_context=self._destination_context(current),
                    recent_messages=recent,
                    changed_fields=[],
                    intent=intent,
                    was_ready=current.is_ready(),
                    validation_error=self._validation_reply(mode, str(exc)),
                )
        was_ready = current.is_ready()
        destination_error = None
        if intent == "planning" and not mode_boundary and (merged.province or merged.cities):
            try:
                merged = RequirementDraft.model_validate(self.resolver.resolve(merged).requirement)
            except DestinationResolutionError as exc:
                destination_error = str(exc)
        if intent == "planning" and not mode_boundary:
            self.repository.save_requirement(row, merged, confirmed=False)

        missing = self.missing_fields(merged)
        context = self._destination_context(merged)
        if intent == "general" and not mode_boundary and (parsed_draft.province or parsed_draft.cities):
            try:
                topic = self.resolver.resolve(parsed_draft).requirement
                context = self._destination_context(RequirementDraft.model_validate(topic.model_dump()))
            except DestinationResolutionError:
                context = None
        changed_fields = self.changed_fields(current, merged)
        return PreparedConversationTurn(
            row=row,
            conversation=self._to_model(self.repository.get(conversation_id, with_messages=True)),
            user_message=clean_message,
            requirement=merged,
            missing=missing,
            destination_context=context,
            recent_messages=recent,
            changed_fields=changed_fields,
            intent=intent,
            was_ready=was_ready,
            should_invite_more=(
                (
                    intent == "planning"
                    and not mode_boundary
                )
                and more_details_prompt
            ),
            should_summarize=(
                intent == "planning"
                and not mode_boundary
                and destination_error is None
                and merged.is_ready()
                and (not was_ready or merged != current)
            ),
            destination_error=destination_error,
            mode_boundary=mode_boundary,
        )

    def _parse_requirement(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, str]],
    ):
        """Parse one turn with the complete prior conversation as context."""

        parse = self.parser.parse
        try:
            parameters = signature(parse).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        supports_context = any(
            parameter.name == "conversation_history"
            or parameter.kind is Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_context:
            return parse(message, conversation_history=conversation_history)
        # Keep legacy deterministic/test parsers that still implement only
        # parse(text) usable. The production RequirementParsingService accepts
        # the context and forwards it to the LangChain/DeepSeek parser.
        return parse(message)

    @staticmethod
    def _validation_reply(mode: str | None, message: str) -> str:
        if mode == "general":
            return "当前是普通模式，不会把这段内容整理成旅行计划；如需制定行程，请先切换到规划模式。"
        if "出发日期" in message:
            return f"{message}。请改为明天或之后的日期，我再继续帮你整理。"
        if "时间" in message:
            return f"{message}。请提供同一天内的开始和结束时间，例如每天08:00到21:00。"
        if "单城市" in message:
            return f"{message}。4～7天请改为同省多城市安排。"
        if "多城市" in message:
            return f"{message}。1～3天请按一个城市安排；如果要游多个城市，请把天数增加到4天以上。"
        return f"{message}。请改成1～7天以内的行程；单城市最多3天，超过3天请选择同省多城市。"

    def finish_message(self, prepared: PreparedConversationTurn, reply: str) -> Conversation:
        self.repository.add_message(prepared.row, role="assistant", content=reply)
        refreshed = self.repository.get(prepared.conversation.id, with_messages=True)
        return self._to_model(refreshed)

    def update_requirement(self, conversation_id: str, changes: dict) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        current = load_requirement_draft(row.requirement_draft or {})
        updated = current.model_copy(update=changes)
        updated = RequirementDraft.model_validate(updated)
        if updated.province or updated.cities:
            updated = RequirementDraft.model_validate(self.resolver.resolve(updated).requirement)
        self.repository.save_requirement(row, updated, confirmed=False)
        return self._to_model(self.repository.get(conversation_id, with_messages=True))

    def confirm(self, conversation_id: str, *, expected_requirement: RequirementDraft | None = None) -> Conversation:
        locked_get = getattr(self.repository, "get_for_confirmation", None)
        row = (locked_get(conversation_id) if locked_get is not None else
               self.repository.get(conversation_id, with_messages=True))
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        requirement = load_requirement_draft(row.requirement_draft or {})
        if expected_requirement is not None and expected_requirement != requirement:
            raise RequirementConflictError("需求已经修改，这张确认卡片已过期，请刷新需求后重新确认")
        if requirement.province or requirement.cities:
            try:
                requirement = RequirementDraft.model_validate(self.resolver.resolve(requirement).requirement)
            except ValueError as exc:
                raise RequirementNotReadyError(str(exc)) from exc
        missing = self.missing_fields(requirement)
        if missing:
            labels = {
                "destination": "目的地",
                "days": "旅行天数",
                "preferences": "兴趣或体验偏好",
                "preference_text": "旅行节奏或体验偏好",
                "start_date": "出发日期",
                "daily_time_window": "每日游玩开始和结束时间",
                "travel_scope": "城市安排方式",
                "city_preference": "具体城市或城市推荐方式",
            }
            raise RequirementNotReadyError("还需要补充：" + "、".join(labels[item] for item in missing))
        self.repository.save_requirement(row, requirement, confirmed=True)
        return self._to_model(self.repository.get(conversation_id, with_messages=True))

    @staticmethod
    def missing_fields(requirement: RequirementDraft) -> list[str]:
        missing = []
        if (
            not requirement.province
            and not requirement.cities
            and not requirement.province_preference_confirmed
        ):
            missing.append("destination")
        if requirement.days is None:
            missing.append("days")
        if (
            requirement.days is not None
            and not requirement.cities
            and not requirement.city_preference_confirmed
        ):
            missing.append("city_preference")
        if not requirement.interests:
            missing.append("preferences")
        elif not is_meaningful_preference_text(
            requirement.preference_text,
            requirement.interests,
        ):
            missing.append("preference_text")
        if requirement.start_date is None:
            missing.append("start_date")
        if requirement.daily_start_time is None or requirement.daily_end_time is None:
            missing.append("daily_time_window")
        return missing

    @staticmethod
    def suggestions_for(
        missing: list[str],
        requirement: RequirementDraft | None = None,
        *,
        intent: str = "planning",
    ) -> list[str]:
        if intent == "general":
            return ["你能做什么？", "介绍一个目的地"]
        if not missing:
            return ["确认生成计划"]
        if missing[0] == "destination":
            return [*DESTINATION_SUGGESTIONS, "你来推荐一个省份"]
        if missing[0] == "days":
            return ConversationService._day_suggestions(requirement)
        if missing[0] == "preferences":
            return ["自然风光", "历史文化", "古城", "摄影", "美食", "休闲慢游"]
        if missing[0] == "preference_text":
            return [
                "节奏轻松，不赶时间",
                "喜欢拍照和沉浸体验",
                "优先游览代表性景点",
            ]
        if missing[0] == "start_date":
            return ["明天出发", "后天出发", "下周一出发"]
        if missing[0] == "daily_time_window":
            return ["每天08:00到21:00", "每天09:00到18:00", "每天10:00到20:00"]
        if missing[0] == "travel_scope":
            if requirement is not None and requirement.days is not None and requirement.days <= MAX_SINGLE_CITY_DAYS:
                return ["一个城市深度游", "增加到4天以上游多个城市"]
            return ["同省多城市"]
        if missing[0] == "city_preference":
            return ["没有指定城市，你来推荐"]
        return []

    @staticmethod
    def _day_suggestions(requirement: RequirementDraft | None) -> list[str]:
        if requirement is not None and requirement.travel_scope == "multi_city":
            return ["4天", "5天", "6天", "7天"]
        if requirement is not None and (
            requirement.travel_scope == "single_city" or len(requirement.cities) == 1
        ):
            return ["1天", "2天", "3天"]
        return ["1天", "2天", "3天", "4天", "5天", "6天", "7天"]

    @staticmethod
    def validation_suggestions(
        message: str,
        requirement: RequirementDraft | None = None,
    ) -> list[str]:
        """Keep correction chips aligned with the failed scope boundary."""

        if "多城市" in message and ("只支持单城市" in message or "至少需要" in message):
            return ["4天", "5天", "6天", "7天"]
        if "单城市规划目前只支持" in message:
            return ["4天", "5天", "6天", "7天"]
        return ConversationService._day_suggestions(requirement)

    @classmethod
    def mode_boundary_reply(
        cls,
        mode: str | None,
        message: str,
        parsed: RequirementDraft,
    ) -> str | None:
        """Keep explicit UI modes isolated from one another.

        Requests from older clients may omit ``mode`` and continue using the
        historical automatic intent detection. Once the UI sends a mode, the
        selected mode is authoritative and a request from the other domain is
        stopped before it can mutate the requirement or call the LLM branch.
        """
        if mode not in {"general", "planning"}:
            return None
        planning_signal = cls.has_planning_signal(message, parsed)
        general_signal = cls.has_general_signal(message)
        if mode == "general" and planning_signal:
            return "当前是普通模式，我不会把这段对话整理成旅行需求。若要制定行程，请先切换到规划模式。"
        if mode == "planning" and general_signal and not planning_signal:
            return "现在是规划模式，我会一步步帮你确定出行条件并生成行程。想先了解目的地或随便聊天，请切换到普通模式。"
        return None

    @staticmethod
    def has_planning_signal(message: str, parsed: RequirementDraft) -> bool:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip()).lower()
        planning_markers = (
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
            or any(marker in normalized for marker in planning_markers)
            or parsed.days is not None
            or parsed.interests
            or parsed.preference_text
            or parsed.start_date is not None
            or parsed.daily_start_time is not None
            or parsed.daily_end_time is not None
            or parsed.city_preference_confirmed
            or parsed.province_preference_confirmed
        )

    @staticmethod
    def has_general_signal(message: str) -> bool:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip()).lower()
        general_markers = (
            "你是谁",
            "你能做什么",
            "你会做什么",
            "介绍一下你",
            "你是什么",
            "你好",
            "嗨",
            "您好",
            "谢谢",
            "感谢",
            "介绍",
            "说说",
            "讲讲",
            "了解",
            "有什么",
            "好玩吗",
            "怎么样",
            "值得去吗",
            "适合什么时候",
        )
        return any(marker in normalized for marker in general_markers)

    @staticmethod
    def responder_inputs(prepared: PreparedConversationTurn) -> dict[str, object]:
        if prepared.intent == "general":
            return {
                "requirement": RequirementDraft(),
                "missing_fields": [],
                "destination_context": prepared.destination_context,
                "changed_fields": [],
            }
        return {
            "requirement": prepared.requirement,
            "missing_fields": prepared.missing,
            "destination_context": prepared.destination_context,
            "changed_fields": prepared.changed_fields,
        }

    @staticmethod
    def detect_intent(
        message: str,
        parsed: RequirementDraft,
        current: RequirementDraft,
    ) -> str:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip()).lower()
        planning_markers = (
            "想去",
            "要去",
            "准备去",
            "打算去",
            "计划去",
            "去玩",
            "旅游",
            "旅行",
            "规划",
            "安排",
            "出发",
            "玩几天",
            "住几天",
        )
        general_markers = (
            "你是谁",
            "你能做什么",
            "你会做什么",
            "介绍一下你",
            "你是什么",
            "你好",
            "嗨",
            "您好",
            "谢谢",
            "感谢",
            "介绍",
            "说说",
            "讲讲",
            "了解一下",
            "有什么特色",
            "有什么特点",
            "好玩吗",
            "怎么样",
            "值得去吗",
            "适合什么时候",
        )
        direct_trip = re.match(
            r"^(?:明天|后天|大后天|下周[一二三四五六日天]|周[一二三四五六日天])?去",
            normalized,
        )
        if direct_trip or any(marker in normalized for marker in planning_markers):
            return "planning"
        if any(marker in normalized for marker in general_markers):
            return "general"
        if any(
            (
                parsed.days is not None,
                bool(parsed.interests),
                bool(parsed.preference_text),
                parsed.start_date is not None,
                parsed.daily_start_time is not None,
                parsed.daily_end_time is not None,
                parsed.city_preference_confirmed,
                parsed.province_preference_confirmed,
            )
        ):
            return "planning"
        if parsed.province or parsed.cities:
            # A bare “杭州/云南” is normally the answer to the first planning
            # question; prose such as “云南不错” stays a casual conversation.
            destinations = {
                value.replace("自治区", "").replace("省", "").replace("市", "")
                for value in [parsed.province, *parsed.cities]
                if value
            }
            bare_destination = normalized in destinations or normalized in {
                f"{value}省" for value in destinations
            } | {f"{value}市" for value in destinations}
            if bare_destination or not (current.province or current.cities):
                return "planning"
        return "general"

    @staticmethod
    def is_more_details_prompt(message: str) -> bool:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        return normalized in {
            "我还想补充",
            "我想补充",
            "我想再补充",
            "再补充一下",
            "继续补充",
        }

    @staticmethod
    def confirms_city_recommendation(message: str) -> bool:
        """Recognize a city-recommendation answer in the city-choice turn."""

        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        if not normalized or ("省份" in normalized and "城市" not in normalized):
            return False
        return any(
            marker in normalized
            for marker in (
                "没有指定城市",
                "没有特别想去的城市",
                "没有想去的城市",
                "不指定城市",
                "不挑城市",
                "城市都可以",
                "城市随便",
                "随便安排城市",
                "推荐城市",
                "你来推荐",
                "由你推荐",
            )
        )

    @staticmethod
    def is_city_addition_message(message: str) -> bool:
        """Recognize a follow-up that adds a city to existing destinations."""

        # The deterministic parser only sees the current turn. Keep the
        # previous cities when the user explicitly says that another city is
        # also/ additionally part of the trip, instead of replacing them with
        # the one newly mentioned city.
        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        return any(
            marker in normalized
            for marker in (
                "也想去",
                "也要去",
                "还想去",
                "还要去",
                "另外想去",
                "另外要去",
                "再去",
                "再加",
                "加上",
                "加一个",
                "增加",
                "顺便去",
                "一起去",
            )
        )

    @staticmethod
    def confirms_province_recommendation(message: str) -> bool:
        """Recognize an explicit request for the assistant to choose a province."""

        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        return "省份" in normalized and any(
            marker in normalized
            for marker in ("你来推荐", "由你推荐", "帮我推荐", "推荐一个省份")
        )

    @staticmethod
    def more_details_reply(requirement: RequirementDraft) -> str:
        missing = ConversationService.missing_fields(requirement)
        labels = {
            "destination": "目的地",
            "days": "旅行天数",
            "preferences": "兴趣或体验偏好",
            "preference_text": "旅行节奏或体验偏好",
            "start_date": "出发日期",
            "daily_time_window": "每日游玩开始和结束时间",
            "travel_scope": "城市安排方式",
            "city_preference": "具体城市或城市推荐方式",
        }
        if not missing:
            return "目前所有出行条件都已经确定了。你可以直接回复“确认生成计划”；如果要修改，直接告诉我具体哪一项。"
        next_field = labels[missing[0]]
        prompts = {
            "destination": "你已经想好去哪个省份或城市了吗？如果还没想好，可以回复“你来推荐一个省份”，我会结合你的兴趣和偏好帮你选。",
            "days": "这次准备玩几天？",
            "preferences": "请先选择这次旅行的兴趣标签，例如自然、历史文化、古城、摄影、美食或休闲。",
            "preference_text": "除了兴趣标签，你希望行程是什么节奏、侧重什么体验？请填写一项明确偏好，例如“节奏轻松，不赶时间”“喜欢拍照和沉浸体验”或“优先游览代表性景点”。",
            "start_date": "计划哪天出发？出发日期至少从明天开始，可以说“明天”“后天”或“下周一”。",
            "daily_time_window": "每天准备几点开始、几点结束游玩？例如“每天早上8点到晚上9点”。",
            "travel_scope": "想在一个城市深度游，还是安排同省多个城市？",
            "city_preference": "有没有特别想去的城市？可以直接说城市名；如果没有，回复“你来推荐”即可。",
        }
        if missing[0] == "travel_scope":
            if requirement.days is not None and requirement.days <= MAX_SINGLE_CITY_DAYS:
                prompts["travel_scope"] = (
                    f"{requirement.days}天更适合安排一个城市深度游；如果想游玩多个城市，建议把天数增加到4天以上。"
                    "你想按一个城市安排，还是增加天数游多个城市？"
                )
            else:
                prompts["travel_scope"] = "4～7天必须安排同省多城市，你确认按同省多城市规划吗？"
        return f"可以。现在还需要确定{next_field}：{prompts[missing[0]]}"

    @staticmethod
    def changed_fields(
        before: RequirementDraft,
        after: RequirementDraft,
    ) -> list[str]:
        fields = (
            "province",
            "cities",
            "days",
            "interests",
            "preference_text",
            "travel_scope",
            "province_preference_confirmed",
            "city_preference_confirmed",
            "start_date",
            "daily_start_time",
            "daily_end_time",
        )
        return [field for field in fields if getattr(before, field) != getattr(after, field)]

    def _merge(
        self,
        current: RequirementDraft,
        parsed: RequirementDraft,
        *,
        message: str = "",
    ) -> RequirementDraft:
        raw_interests = canonicalize_interests([*current.interests, *parsed.interests])
        interests, _ = self._normalize_interests(raw_interests)
        scope = parsed.travel_scope
        if scope == "auto" and current.travel_scope != "auto":
            scope = current.travel_scope
        days = parsed.days or current.days
        if parsed.cities and current.cities and self.is_city_addition_message(message):
            # A turn such as “昆明我突然也想去” parses to only [昆明]. Keep
            # the already confirmed cities because this turn is an addition,
            # not a replacement of the destination list.
            cities = list(dict.fromkeys([*current.cities, *parsed.cities]))
        else:
            cities = parsed.cities or current.cities
        # The merged city list is authoritative. A single-city parser result
        # must not turn an existing 4～7 day multi-city trip into an invalid
        # single-city draft.
        if len(cities) >= 2:
            scope = "multi_city"
        elif scope == "auto" and days is not None:
            scope = "single_city" if days <= MAX_SINGLE_CITY_DAYS else "multi_city"
        city_preference_confirmed = (
            current.city_preference_confirmed
            or parsed.city_preference_confirmed
            or bool(parsed.cities)
        )
        if parsed.province_preference_confirmed and not parsed.province and not parsed.cities:
            # The generic phrase “你来推荐” is also accepted for cities. An
            # explicit province recommendation request must not accidentally
            # settle the later city-choice step as well.
            city_preference_confirmed = current.city_preference_confirmed
        province_preference_confirmed = (
            current.province_preference_confirmed
            or parsed.province_preference_confirmed
            or bool(parsed.province)
        )
        if parsed.province and parsed.province != current.province and not parsed.cities:
            cities = []
            province_preference_confirmed = True
            # Changing province invalidates an old named-city choice, but an
            # explicit “城市你来推荐” in the same message remains valid.
            city_preference_confirmed = parsed.city_preference_confirmed
        elif parsed.cities:
            # A named city settles the destination directly; it should not
            # remain labelled as an AI-recommended province.
            province_preference_confirmed = False
        return RequirementDraft(
            province=parsed.province or current.province,
            cities=cities,
            province_preference_confirmed=province_preference_confirmed,
            city_preference_confirmed=city_preference_confirmed,
            days=days,
            interests=interests,
            preference_text=parsed.preference_text or current.preference_text,
            travel_scope=scope,
            start_date=parsed.start_date or current.start_date,
            daily_start_time=parsed.daily_start_time or current.daily_start_time,
            daily_end_time=parsed.daily_end_time or current.daily_end_time,
            transport_mode="auto",
        )

    def _normalize_interests(self, interests: list[str]) -> tuple[list[str], list[str]]:
        normalizer = getattr(self.repository, "normalize_interest_tags", None)
        if callable(normalizer):
            return normalizer(interests)

        tags: list[str] = []
        for province in self.resolver.repository.list_provinces():
            tags.extend(province.tags or [])
            for city in self.resolver.repository.list_cities_by_province(province.name):
                tags.extend(city.tags or [])
        return normalize_interest_tags(interests, tags)

    def _destination_context(self, requirement: RequirementDraft) -> str | None:
        if requirement.cities:
            city = self.resolver.repository.find_city(requirement.cities[0])
            return city.description if city else None
        if requirement.province:
            province = self.resolver.repository.find_province(requirement.province)
            return province.description if province else None
        return None

    @staticmethod
    def summary_reply(
        requirement: RequirementDraft,
        *,
        changed_fields: list[str] | None = None,
        was_ready: bool = False,
    ) -> str:
        destination = "、".join(requirement.cities) if requirement.cities else requirement.province
        if not destination and requirement.province_preference_confirmed:
            destination = "AI根据兴趣和偏好推荐省份"
        destination = destination or "待补充"
        profile_parts = ["、".join(requirement.interests)] if requirement.interests else []
        if requirement.preference_text and requirement.preference_text != NO_EXTRA_PREFERENCE_TEXT:
            profile_parts.append(requirement.preference_text)
        interests = " · ".join(profile_parts) or requirement.preference_text or "待补充"
        start_date = format_date_zh(requirement.start_date) if requirement.start_date else None
        scope = {
            "single_city": "单城市深度游",
            "multi_city": "同省多城市",
            "auto": "待确定",
        }[requirement.travel_scope]
        city_preference_sentence = (
            "具体城市由 AI 根据你的偏好推荐。"
            if not requirement.cities and requirement.city_preference_confirmed
            else ""
        )
        province_preference_sentence = (
            "省份由 AI 根据你的兴趣和偏好推荐。"
            if not requirement.province
            and not requirement.cities
            and requirement.province_preference_confirmed
            else ""
        )
        opening = (
            "收到，我把刚才的调整记下了。现在的旅行画像是："
            if was_ready and changed_fields
            else "我已经抓住这次旅行的重点了："
        )
        date_sentence = f"出发时间安排在{start_date}。" if start_date else "出发日期待确定。"
        daily_time_sentence = (
            f"每天游玩时间为{requirement.daily_start_time}—{requirement.daily_end_time}。"
            if requirement.daily_start_time and requirement.daily_end_time
            else "每日游玩开始和结束时间待确定。"
        )
        return (
            f"{opening}\n"
            f"{destination} · {requirement.days}天 · {interests}\n"
            f"按{scope}安排，{province_preference_sentence}{city_preference_sentence}景点间路线由地图工具根据实际位置自动推荐。{date_sentence}{daily_time_sentence}\n\n"
            "以上出行条件全部确定后，才会生成计划。请回复“确认生成计划”；想调整任何一项，直接告诉我就好。"
        )

    @staticmethod
    def _fallback_reply(requirement: RequirementDraft, missing: list[str], context: str | None) -> str:
        if not missing:
            return ConversationService.summary_reply(requirement)
        destination = "、".join(requirement.cities) if requirement.cities else requirement.province
        recommending_province = not destination and requirement.province_preference_confirmed
        if missing[0] == "destination":
            return (
                "我们先确定目的地。你已经想好去哪个省份或城市了吗？"
                "如果还没想好，可以回复“你来推荐一个省份”，我会结合你的兴趣和偏好帮你选。"
            )
        if missing[0] == "days":
            if recommending_province:
                return "可以，我会根据你的兴趣和偏好推荐省份。这次准备玩几天？"
            intro = f"{destination}，收到。" if destination else "收到。"
            if context:
                intro += f"{context[:56].rstrip('，。；')}。"
            return f"{intro}这次准备玩几天？"
        if missing[0] == "preferences":
            scope_hint = "按同省多城市安排。" if requirement.days and requirement.days > MAX_SINGLE_CITY_DAYS else ""
            destination_text = "推荐的省份" if recommending_province else (destination or "目的地")
            return (
                f"{destination_text}安排{requirement.days}天，{scope_hint}先选择这次旅行的兴趣标签："
                "自然、历史文化、古城、摄影、美食或休闲。"
            )
        if missing[0] == "preference_text":
            return (
                "兴趣方向已经记下了。除了这些标签，你希望行程是什么节奏、侧重什么体验？"
                "这一项需要明确填写，例如“节奏轻松，不赶时间”“喜欢拍照和沉浸体验”"
                "或“优先游览代表性景点”。"
            )
        if missing[0] == "start_date":
            return "计划哪天出发？可以说具体日期，也可以说“明天”或“下周一”。"
        if missing[0] == "daily_time_window":
            return "每天准备几点开始、几点结束游玩？例如“每天早上8点到晚上9点”。"
        if missing[0] == "travel_scope":
            if requirement.days is not None and requirement.days <= MAX_SINGLE_CITY_DAYS:
                return (
                    f"{requirement.days}天更适合安排一个城市深度游；如果想游玩多个城市，建议把天数增加到4天以上。"
                    "你想按一个城市安排，还是增加天数游多个城市？"
                )
            return "4～7天必须安排同省多城市，请确认按同省多城市规划。"
        if missing[0] == "city_preference":
            if requirement.travel_scope == "multi_city":
                return "这次会安排同省多城市。有没有特别想去的城市？可以直接说城市名；如果没有，回复“你来推荐”即可。"
            return "有没有特别想去的城市？可以直接说城市名；如果没有，回复“你来推荐”即可。"
        return "请补充完整的出行条件，我再为你生成计划。"

    @staticmethod
    def _general_fallback_reply(message: str, context: str | None) -> str:
        normalized = re.sub(r"[，。！？、\s]", "", message.strip())
        if any(marker in normalized for marker in ("你是谁", "你能做什么", "你会做什么", "介绍一下你")):
            return "我是“山”，一个可以陪你聊目的地、风景、人文、美食和旅行灵感的 AI 旅行助手。需要制定具体行程时，请切换到规划模式。"
        if any(marker in normalized for marker in ("你好", "嗨", "您好")):
            return "你好，我是“山”。普通模式可以聊目的地和旅行灵感；需要制定具体行程时，请切换到规划模式。"
        if any(marker in normalized for marker in ("谢谢", "感谢")):
            return "不客气，慢慢来就好。你想到什么就问什么。"
        if context:
            return f"{context.rstrip('，。；')}。如果你想继续了解，我也可以从当地的风景、人文、美食或适合的旅行方式展开。"
        return "可以先聊聊目的地、风景、人文、美食或旅行灵感。需要制定具体行程时，请切换到规划模式。"

    @staticmethod
    def _to_model(row) -> Conversation:
        requirement = load_requirement_draft(row.requirement_draft or {})
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
