from collections.abc import Sequence
from dataclasses import dataclass

from backend.attractions.dedupe import dedupe_attractions
from backend.models.itinerary import CityPlan, CityPlanningResult
from backend.models.planning import CandidateDecision, PlanningCandidate
from backend.models.retrieval import HybridSearchResult
from backend.models.route import RouteSegment
from backend.planner.city import CityPlanningError, deduplicate_city_plan
from backend.planner.dynamic_scheduler import (
    DynamicSchedulingError,
    dynamic_city_plan,
    dynamic_city_plan_with_routes,
)


@dataclass(frozen=True)
class RouteAwareCityPlanningResult:
    """Internal result used to carry prevalidated routes into the workflow."""

    city_result: CityPlanningResult
    routes_by_day: list[list[RouteSegment]]
    decisions: list[CandidateDecision]


class CityPlanningService:
    def __init__(
        self,
        *,
        retrieval_service,
        planner,
        route_enrichment_service=None,
        schedule_builder=None,
        max_route_attempts_per_step: int = 5,
    ):
        self.retrieval_service = retrieval_service
        self.planner = planner
        self.route_enrichment_service = route_enrichment_service
        self.schedule_builder = schedule_builder
        self.max_route_attempts_per_step = max(1, int(max_route_attempts_per_step))

    async def retrieve_candidates(
        self,
        *,
        city_stay,
        requirement,
        top_k: int = 20,
    ) -> list[HybridSearchResult]:
        """Retrieve and stably deduplicate the local candidate pool."""

        return dedupe_attractions(
            await self.retrieval_service.search(
                city=city_stay.city,
                interests=requirement.interests,
                preference_text=requirement.preference_text,
                top_k=top_k,
            )
        )

    async def plan(self, *, city_stay, requirement, top_k: int = 20) -> CityPlanningResult:
        attractions = await self.retrieve_candidates(
            city_stay=city_stay,
            requirement=requirement,
            top_k=top_k,
        )
        return await self._plan_from_attractions(
            city_stay=city_stay,
            requirement=requirement,
            attractions=attractions,
        )

    async def plan_with_routes(
        self,
        *,
        city_stay,
        requirement,
        city_adcode: str,
        attractions: Sequence[HybridSearchResult],
    ) -> RouteAwareCityPlanningResult:
        """Plan from enriched candidates while validating routes before selection."""

        if self.route_enrichment_service is None or self.schedule_builder is None:
            raise CityPlanningError("路线感知排程未完成服务配置")

        unique_attractions = dedupe_attractions(list(attractions))
        candidates = self._to_planning_candidates(unique_attractions)
        try:
            dynamic_result = await dynamic_city_plan_with_routes(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
                city_adcode=city_adcode,
                route_service=self.route_enrichment_service,
                schedule_builder=self.schedule_builder,
                max_route_attempts_per_step=self.max_route_attempts_per_step,
            )
        except (DynamicSchedulingError, ValueError) as exc:
            raise CityPlanningError(str(exc)) from exc

        city_plan = await self._apply_narration(
            city_stay=city_stay,
            requirement=requirement,
            candidates=candidates,
            city_plan=dynamic_result.city_plan,
        )
        city_plan = deduplicate_city_plan(plan=city_plan, candidates=candidates)
        return RouteAwareCityPlanningResult(
            city_result=CityPlanningResult(
                city_stay=city_stay,
                attractions=unique_attractions,
                city_plan=city_plan,
            ),
            routes_by_day=dynamic_result.routes_by_day,
            decisions=dynamic_result.decisions,
        )

    async def _plan_from_attractions(
        self,
        *,
        city_stay,
        requirement,
        attractions: Sequence[HybridSearchResult],
    ) -> CityPlanningResult:
        unique_attractions = dedupe_attractions(list(attractions))
        candidates = self._to_planning_candidates(unique_attractions)
        try:
            dynamic_result = dynamic_city_plan(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
            )
        except DynamicSchedulingError as exc:
            # Keep the service/API error contract stable when retrieval does
            # not leave any usable local candidates for this city.
            raise CityPlanningError(str(exc)) from exc

        city_plan = await self._apply_narration(
            city_stay=city_stay,
            requirement=requirement,
            candidates=candidates,
            city_plan=dynamic_result.city_plan,
        )
        city_plan = deduplicate_city_plan(plan=city_plan, candidates=candidates)
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=unique_attractions,
            city_plan=city_plan,
        )

    async def _apply_narration(
        self,
        *,
        city_stay,
        requirement,
        candidates: list[PlanningCandidate],
        city_plan: CityPlan,
    ) -> CityPlan:
        # The deterministic scheduler owns the attraction list. The optional
        # LLM planner may still provide natural-language day descriptions, but
        # it can no longer choose or reorder the actual POIs.
        try:
            narrated_plan = await self.planner.plan(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
            )
        except CityPlanningError:
            narrated_plan = None

        if narrated_plan is not None and len(narrated_plan.days) == len(city_plan.days):
            city_plan = city_plan.model_copy(
                update={
                    "days": [
                        day.model_copy(update={"description": narrated_day.description})
                        for day, narrated_day in zip(
                            city_plan.days,
                            narrated_plan.days,
                            strict=True,
                        )
                    ]
                }
            )
        return city_plan

    @staticmethod
    def _to_planning_candidates(
        attractions: Sequence[HybridSearchResult],
    ) -> list[PlanningCandidate]:
        return [
            PlanningCandidate(
                poi_id=item.poi_id,
                name=item.name,
                city=item.city,
                category=item.category,
                interest_tags=list(item.interest_tags),
                longitude=item.longitude,
                latitude=item.latitude,
                description=item.description,
                source_url=item.source_url,
                visit_duration_minutes=item.visit_duration_minutes,
                tag_match_count=item.tag_match_count,
                semantic_score=item.preference_similarity,
                keyword_score=item.keyword_score,
                preference_score=item.preference_score,
            )
            for item in attractions
        ]


__all__ = ["CityPlanningService", "RouteAwareCityPlanningResult"]
