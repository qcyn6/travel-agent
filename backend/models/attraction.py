from typing import Literal

from pydantic import BaseModel, ConfigDict


KnowledgeStatus = Literal["pending", "processing", "ready", "failed"]


class AttractionPOI(BaseModel):
    """Provider-independent attraction candidate used by later retrieval stages."""

    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    rating: float | None = None


class AttractionKnowledgeState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    poi_id: str
    name: str
    knowledge_status: KnowledgeStatus
    knowledge_error: str | None = None
