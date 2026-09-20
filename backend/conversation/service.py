from dataclasses import dataclass
from inspect import isawaitable
import re

from backend.conversation.responder import ConversationResponseError
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
from backend.tag_matching import normalize_interest_tags


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
    changed_fields: list[str]
    intent: str = "planning"
    was_ready: bool = False
    should_summarize: bool = False
    should_invite_more: bool = False
    destination_error: str | None = None
    mode_boundary: str | None = None
    validation_error: str | None = None


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

    async def handle_message(
        self,
        conversation_id: str,
        message: str,
        *,
        mode: str | None = None,
    ) -> tuple[Conversation, str, list[str]]:
        prepared = await self.prepare_message(conversation_id, message, mode=mode)
        if prepared.mode_boundary:
            reply = prepared.mode_boundary
        elif prepared.validation_error:
            reply = prepared.validation_error
        elif prepared.destination_error:
            reply = (
                f"{prepared.destination_error}。换一个已收录的国内省份或城市试试，"
                "也可以让我根据你的兴趣和偏好推荐一个省份。"
            )
        elif prepared.should_invite_more:
            reply = self.more_details_reply(prepared.requirement)
        elif prepared.should_summarize:
            reply = self.summary_reply(
                prepared.requirement,
                changed_fields=prepared.changed_fields,
                was_ready=prepared.was_ready,
            )
        elif self.should_explain_multi_city_boundary(prepared):
            reply = self._fallback_reply(
                prepared.requirement,
                prepared.missing,
                prepared.destination_context,
            )
        elif prepared.intent == "general":
            try:
                response_inputs = self.responder_inputs(prepared)
                reply = await self.responder.respond(
                    user_message=prepared.user_message,
                    **response_inputs,
                    recent_messages=prepared.recent_messages,
                    conversation_mode="general",
                )
            except ConversationResponseError:
                reply = self._general_fallback_reply(
                    prepared.user_message,
                    prepared.destination_context,
                )
        elif (
            not (prepared.requirement.province or prepared.requirement.cities)
            and not prepared.requirement.province_preference_confirmed
        ):
            reply = self._fallback_reply(
                prepared.requirement,
                prepared.missing,
                prepared.destination_context,
            )
        elif prepared.missing and prepared.missing[0] == "city_preference":
            # Keep the confirmation chip and the assistant text driven by the
            # same deterministic state. The LLM must not claim that all fields
            # are complete while the city recommendation choice is pending.
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
                    changed_fields=prepared.changed_fields,
                )
            except ConversationResponseError:
                reply = self._fallback_reply(
                    prepared.requirement,
                    prepared.missing,
                    prepared.destination_context,
                )
        conversation = self.finish_message(prepared, reply)
        suggestions = self.suggestions_for(
            prepared.missing,
            prepared.requirement,
            intent=prepared.intent,
        )
        if prepared.validation_error and prepared.intent == "planning":
            suggestions = self.validation_suggestions(
                prepared.validation_error,
                prepared.requirement,
            )
        return conversation, reply, suggestions

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
        recent = [
            {"role": item.role, "content": item.content}
            for item in row.messages[-8:]
            if item.role in {"user", "assistant"}
        ]
        self.repository.add_message(row, role="user", content=clean_message)

        current = load_requirement_draft(row.requirement_draft or {})
        try:
            parsed_value = self.parser.parse(message)
            parsed = await parsed_value if isawaitable(parsed_value) else parsed_value
            parsed_draft = RequirementDraft.model_validate(parsed.model_dump())
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
                merged = self._merge(current, parsed_draft)
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

    def confirm(self, conversation_id: str) -> Conversation:
        row = self.repository.get(conversation_id, with_messages=True)
        if row is None:
            raise ConversationNotFoundError("当前会话不存在或已失效")
        requirement = load_requirement_draft(row.requirement_draft or {})
        missing = self.missing_fields(requirement)
        if missing:
            labels = {
                "destination": "目的地",
                "days": "旅行天数",
                "preferences": "兴趣或体验偏好",
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
        if requirement.travel_scope == "auto":
            missing.append("travel_scope")
        elif not requirement.cities and not requirement.city_preference_confirmed:
            missing.append("city_preference")
        if not requirement.interests and not requirement.preference_text:
            missing.append("preferences")
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
    def more_details_reply(requirement: RequirementDraft) -> str:
        missing = ConversationService.missing_fields(requirement)
        labels = {
            "destination": "目的地",
            "days": "旅行天数",
            "preferences": "兴趣或体验偏好",
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
            "preferences": "你更想体验自然风光、历史文化、美食、摄影，还是轻松慢游？",
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

    def _merge(self, current: RequirementDraft, parsed: RequirementDraft) -> RequirementDraft:
        raw_interests = canonicalize_interests([*current.interests, *parsed.interests])
        interests, _ = self._normalize_interests(raw_interests)
        scope = parsed.travel_scope
        if scope == "auto" and current.travel_scope != "auto":
            scope = current.travel_scope
        days = parsed.days or current.days
        if scope == "auto" and days is not None and days > MAX_SINGLE_CITY_DAYS:
            scope = "multi_city"
        cities = parsed.cities or current.cities
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
            city_preference_confirmed = False
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
        interests = "、".join(requirement.interests) if requirement.interests else requirement.preference_text or "待补充"
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
                f"{destination_text}安排{requirement.days}天，{scope_hint}可以把节奏做得更舒服。"
                "你更想把重点放在自然风光、历史文化、美食、摄影，还是轻松慢游？"
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
