"""Stable boundaries between workflow orchestration and planning algorithms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class CityPlanningAlgorithm(Protocol):
    """Contract used by LangGraph to invoke city-planning algorithms.

    The existing ``CityPlanningService`` satisfies this contract. A future
    LangChain-backed adapter may implement it, but the graph must not know how
    attractions are selected, routed, or scheduled.
    """

    route_enrichment_service: Any
    schedule_builder: Any

    async def retrieve_candidates(
        self,
        *,
        city_stay: Any,
        requirement: Any,
        top_k: int | None = None,
    ) -> list[Any]: ...

    async def plan(
        self,
        *,
        city_stay: Any,
        requirement: Any,
        top_k: int | None = None,
        narrate: bool = True,
    ) -> Any: ...

    async def plan_from_candidates(
        self,
        *,
        city_stay: Any,
        requirement: Any,
        attractions: Sequence[Any],
        narrate: bool = True,
    ) -> Any: ...

    async def plan_with_routes(
        self,
        *,
        city_stay: Any,
        requirement: Any,
        city_adcode: str,
        attractions: Sequence[Any],
        narrate: bool = True,
    ) -> Any: ...


__all__ = ["CityPlanningAlgorithm"]
