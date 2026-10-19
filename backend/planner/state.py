"""Domain state shared by the planning LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict


class PlanningContext(TypedDict, total=False):
    """Per-run values that must not be checkpointed with domain state.

    Progress callbacks belong to the application worker and are not stable
    data. Keeping them in LangGraph's runtime context means a checkpoint can
    be serialized and restored in another process without attempting to
    serialize an open Python callback or database session.
    """

    progress: Any


class PlanningState(TypedDict, total=False):
    """Raw, structured planning data shared between graph nodes.

    Agent-local message history should stay inside an agent/subgraph. The
    top-level graph carries domain objects so deterministic services can
    validate and reuse them without parsing model text.
    """

    requirement: Any
    progress: Any
    destination: Any
    city_context: dict[str, Any]
    plan: Any
    result: Any
    review: Any
    planning_error: str | None
    planning_retry_count: int
    current_step: str
    retry_counts: dict[str, int]


__all__ = ["PlanningContext", "PlanningState"]
