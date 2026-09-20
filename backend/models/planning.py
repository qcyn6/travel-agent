"""Internal models used between retrieval and deterministic planning.

These models are deliberately separate from the database rows and public
retrieval response.  They form the contract that carries retrieval scores into
the planner without adding any columns to ``attractions``.
"""

from pydantic import BaseModel, ConfigDict, Field

from backend.models.retrieval import AttractionDocument


class PlanningCandidate(AttractionDocument):
    """One locally retrieved attraction together with explainable scores."""

    tag_match_count: int = Field(default=0, ge=0)
    semantic_score: float | None = Field(default=None, ge=0)
    keyword_score: float = Field(default=0, ge=0)
    preference_score: float = Field(default=0, ge=0)


class CandidateDecision(BaseModel):
    """One deterministic scheduler decision for an attraction candidate."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    poi_id: str
    name: str
    preference_score: float = Field(default=0, ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    distance_score: float = Field(default=0, ge=0)
    priority_score: float = Field(default=0, ge=0)
    feasible: bool
    selected: bool = False
    rejected_reason: str | None = None


__all__ = ["CandidateDecision", "PlanningCandidate"]
