from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.route import RouteSegment
from backend.models.requirement import (
    MAX_SINGLE_CITY_DAYS,
    MAX_TRAVEL_DAYS,
    MIN_TRAVEL_DAYS,
    TransportMode,
    TravelRequirement,
    TravelScope,
    validate_start_date,
    validate_travel_days,
)
from backend.requirements.preferences import is_meaningful_preference_text
from backend.time_utils import normalize_clock_time


ConversationStatus = Literal["draft", "confirmed", "planning", "completed"]
ConversationSummaryStatus = Literal["draft", "confirmed", "planning", "completed", "active"]
MessageRole = Literal["user", "assistant"]
ConversationMode = Literal["general", "planning"]
GeneralConversationStatus = Literal["active"]


class RequirementDraft(TravelRequirement):
    """Mutable requirement state for one anonymous conversation."""

    def is_ready(self) -> bool:
        has_destination = bool(self.province or self.cities or self.province_preference_confirmed)
        # Tags decide candidate eligibility; free-form preference text ranks
        # the matched candidates. Both inputs must be explicitly settled.
        has_interests = bool(self.interests)
        has_preference_text = is_meaningful_preference_text(
            self.preference_text,
            self.interests,
        )
        # Both single-city and multi-city plans must settle whether the user
        # names the cities or explicitly lets the planner recommend them.
        has_city_preference = bool(self.cities) or self.city_preference_confirmed
        # A plan may only start after every user-facing planning input has
        # been explicitly settled in the conversation. Route mode stays
        # internal and is always resolved automatically by the map tool.
        return (
            has_destination
            and self.days is not None
            and has_interests
            and has_preference_text
            and self.start_date is not None
            and self.daily_start_time is not None
            and self.daily_end_time is not None
            and self.travel_scope != "auto"
            and has_city_preference
        )


def load_requirement_draft(value: object) -> RequirementDraft:
    """Load a draft without breaking conversations created before the cap."""

    data = dict(value) if isinstance(value, dict) else value
    if isinstance(data, dict):
        # Route mode is an internal planning detail. Normalize old drafts
        # that may have captured a user-selected mode before auto routing was
        # introduced, so every newly generated plan uses map recommendations.
        data["transport_mode"] = "auto"
        if not is_meaningful_preference_text(
            data.get("preference_text"),
            data.get("interests") or [],
        ):
            # Older conversations may have accidentally stored an interest
            # tag, date or time answer as the free-form preference. Reopen
            # that step instead of treating the corrupted value as complete.
            data["preference_text"] = None
        days = data.get("days")
        numeric_days: int | None = None
        if isinstance(days, int) and not isinstance(days, bool):
            numeric_days = days
        elif isinstance(days, str) and days.strip().isdigit():
            numeric_days = int(days.strip())
        if numeric_days is not None and not MIN_TRAVEL_DAYS <= numeric_days <= MAX_TRAVEL_DAYS:
            data["days"] = None
        elif numeric_days is not None:
            cities = data.get("cities") or []
            scope = data.get("travel_scope", "auto")
            if (scope == "single_city" or (len(cities) == 1 and scope == "auto")) and numeric_days > MAX_SINGLE_CITY_DAYS:
                # Old drafts may contain a single-city 4～7 day plan. It is
                # no longer a valid combination, so reopen the day/scope
                # decision instead of failing to load the conversation.
                data["days"] = None
                data["travel_scope"] = "auto"
            elif scope == "multi_city" and numeric_days <= MAX_SINGLE_CITY_DAYS:
                # Older drafts could explicitly store a short multi-city
                # choice. Preserve the user's other answers, but reopen the
                # scope decision under the current 1～3/4～7 product boundary.
                data["travel_scope"] = "auto"
            elif len(cities) >= 2 and numeric_days < len(cities):
                data["days"] = None
        start_date = data.get("start_date")
        if isinstance(start_date, str):
            try:
                start_date = date.fromisoformat(start_date)
            except ValueError:
                start_date = None
        if isinstance(start_date, date) and start_date <= date.today():
            # A historical draft should reopen as incomplete instead of
            # blocking the conversation after the date boundary is tightened.
            data["start_date"] = None
    return RequirementDraft.model_validate(data or {})


class RequirementDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    cities: list[str] | None = None
    province_preference_confirmed: bool | None = None
    city_preference_confirmed: bool | None = None
    days: int | None = Field(
        default=None,
        ge=MIN_TRAVEL_DAYS,
        le=MAX_TRAVEL_DAYS,
        description=f"单次规划支持 1～{MAX_TRAVEL_DAYS} 天",
    )
    interests: list[str] | None = None
    preference_text: str | None = None
    travel_scope: TravelScope | None = None
    start_date: date | None = None
    daily_start_time: str | None = None
    daily_end_time: str | None = None
    transport_mode: TransportMode | None = None

    @field_validator("days", mode="before")
    @classmethod
    def _validate_day_range(cls, value: object) -> object:
        return validate_travel_days(value)

    @field_validator("start_date", mode="before")
    @classmethod
    def _validate_start_date(cls, value: object) -> object:
        return validate_start_date(value)

    @field_validator("daily_start_time", "daily_end_time", mode="before")
    @classmethod
    def _validate_daily_time(cls, value: object, info) -> object:
        label = "每日开始时间" if info.field_name == "daily_start_time" else "每日结束时间"
        return normalize_clock_time(value, label=label)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence_number: int
    role: MessageRole
    content: str
    card: dict[str, object] | None = None
    created_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: ConversationStatus
    mode: ConversationMode = "planning"
    requirement: RequirementDraft
    requirement_ready: bool
    requirement_confirmed: bool
    messages: list[ConversationMessage]
    created_at: datetime
    updated_at: datetime


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    # `/conversations` is the planning-only endpoint. Ordinary chat has its
    # own resource and request model below.
    mode: ConversationMode = "planning"

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("消息不能为空")
        return message


class ConversationCreated(BaseModel):
    id: str
    title: str
    status: ConversationStatus
    requirement: RequirementDraft


class PlanningJobState(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: ConversationSummaryStatus
    mode: ConversationMode = "planning"
    updated_at: datetime


class GeneralConversation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: GeneralConversationStatus
    messages: list[ConversationMessage]
    created_at: datetime
    updated_at: datetime


class GeneralChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)


class GeneralRouteChatCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["route"]
    route: RouteSegment


class GeneralChatCardUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: GeneralRouteChatCard


class GeneralChatCardUpdateResponse(BaseModel):
    content: str


class RequirementConfirmation(BaseModel):
    conversation_id: str
    requirement: RequirementDraft
    confirmed: bool
