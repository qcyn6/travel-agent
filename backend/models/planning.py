"""Internal models used between retrieval and deterministic planning.

These models are deliberately separate from the database rows and public
retrieval response.  They form the contract that carries retrieval scores into
the planner without adding any columns to ``attractions``.
"""

from pydantic import Field

from backend.models.retrieval import AttractionDocument


class PlanningCandidate(AttractionDocument):
    """One locally retrieved attraction together with explainable scores."""

    tag_match_count: int = Field(default=0, ge=0)
    semantic_score: float | None = Field(default=None, ge=0)
    keyword_score: float = Field(default=0, ge=0)
    preference_score: float = Field(default=0, ge=0)


__all__ = ["PlanningCandidate"]
