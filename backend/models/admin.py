from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminConversationSummary(BaseModel):
    id: str
    title: str
    mode: str
    status: str
    user_id: str | None = None
    user_username: str | None = None
    updated_at: datetime | None = None


class GuideDocumentSummary(BaseModel):
    filename: str
    title: str
    size: int
    updated_at: datetime | None = None


class GuideDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    title: str
    content: str


class GuideDocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class GuideDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str


class AttractionKnowledgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str


__all__ = [
    "AdminConversationSummary",
    "AttractionKnowledgeUpdate",
    "GuideDocument",
    "GuideDocumentCreate",
    "GuideDocumentSummary",
    "GuideDocumentUpdate",
]
