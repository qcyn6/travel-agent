from pydantic import BaseModel, ConfigDict, Field

class AttractionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    interest_tags: list[str] = Field(default_factory=list)
    longitude: float | None = None
    latitude: float | None = None
    description: str
    source_url: str | None = None
    visit_duration_minutes: int = Field(default=90, ge=1, le=720)
    opening_hours_weekly: str | None = None
    opening_hours_today: str | None = None
    opening_hours_status: str = "not_checked"

    def index_text(self) -> str:
        parts = [self.name]
        if self.category:
            parts.append(self.category)
        if self.interest_tags:
            parts.append("、".join(self.interest_tags))
        parts.append(self.description)
        return "\n".join(parts)


class VectorSearchResult(BaseModel):
    poi_id: str
    name: str
    city: str
    category: str | None = None
    interest_tags: list[str] = Field(default_factory=list)
    longitude: float | None = None
    latitude: float | None = None
    description: str
    source_url: str | None = None
    visit_duration_minutes: int = Field(default=90, ge=1, le=720)
    distance: float = Field(ge=0)


class KnowledgeSearchResult(BaseModel):
    """A province/city chunk returned by ordinary-chat RAG."""

    doc_id: str
    entity_type: str
    name: str
    province: str
    region_level: str
    source_file: str
    content: str
    distance: float = Field(ge=0)
    # RRF score is useful for adaptive retrieval decisions but is not
    # required by existing callers or persisted API payloads.
    retrieval_score: float = Field(default=0, ge=0)


class IndexBuildResult(BaseModel):
    city: str
    indexed_count: int = Field(ge=0)


class HybridSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    interests: list[str] = Field(default_factory=list)
    preference_text: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class HybridSearchResult(BaseModel):
    poi_id: str
    name: str
    city: str
    category: str | None = None
    interest_tags: list[str] = Field(default_factory=list)
    longitude: float | None = None
    latitude: float | None = None
    description: str
    source_url: str | None = None
    visit_duration_minutes: int = Field(default=90, ge=1, le=720)
    opening_hours_weekly: str | None = None
    opening_hours_today: str | None = None
    opening_hours_status: str = "not_checked"
    # New retrieval observability fields. The legacy rank fields below stay
    # in the API for compatibility with saved plans and existing clients.
    tag_match_count: int = Field(default=0, ge=0)
    preference_similarity: float | None = Field(default=None, ge=0)
    keyword_score: float = Field(default=0, ge=0)
    preference_score: float = Field(default=0, ge=0)
    bm25_rank: int | None = None
    interest_rank: int | None = None
    vector_rank: int | None = None
    rrf_score: float = Field(default=0, ge=0)
    final_rank: int = Field(ge=1)
