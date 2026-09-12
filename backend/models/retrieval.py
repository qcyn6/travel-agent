from pydantic import BaseModel, ConfigDict, Field


class AttractionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str
    name: str
    city: str
    category: str | None = None
    description: str
    source_url: str | None = None

    def index_text(self) -> str:
        parts = [self.name]
        if self.category:
            parts.append(self.category)
        parts.append(self.description)
        return "\n".join(parts)


class VectorSearchResult(BaseModel):
    poi_id: str
    name: str
    city: str
    category: str | None = None
    description: str
    source_url: str | None = None
    distance: float = Field(ge=0)


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
    description: str
    source_url: str | None = None
    bm25_rank: int | None = None
    vector_rank: int | None = None
    rrf_score: float = Field(ge=0)
    final_rank: int = Field(ge=1)
