from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.requirement import TransportMode, TravelRequirement, TravelScope


ConversationStatus = Literal["draft", "confirmed", "planning", "completed"]
MessageRole = Literal["user", "assistant"]


class RequirementDraft(TravelRequirement):
    """Mutable requirement state for one anonymous conversation."""

    def is_ready(self) -> bool:
        has_destination = bool(self.province or self.cities)
        has_preference = bool(self.interests or self.preference_text)
        return has_destination and self.days is not None and has_preference


class RequirementDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = None
    cities: list[str] | None = None
    days: int | None = Field(default=None, ge=1)
    interests: list[str] | None = None
    preference_text: str | None = None
    travel_scope: TravelScope | None = None
    start_date: date | None = None
    transport_mode: TransportMode | None = None


class ConversationMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence_number: int
    role: MessageRole
    content: str
    created_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: ConversationStatus
    requirement: RequirementDraft
    requirement_ready: bool
    requirement_confirmed: bool
    messages: list[ConversationMessage]
    created_at: datetime
    updated_at: datetime


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)


class ConversationCreated(BaseModel):
    id: str
    title: str
    status: ConversationStatus
    requirement: RequirementDraft


class RequirementConfirmation(BaseModel):
    conversation_id: str
    requirement: RequirementDraft
    confirmed: bool
