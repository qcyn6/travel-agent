from collections.abc import Sequence
from dataclasses import dataclass
import logging

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


logger = logging.getLogger(__name__)


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
        opening_hours_service=None,
        max_route_attempts_per_step: int | None = None,
    ):
        self.retrieval_service = retrieval_service
        self.planner = planner
        self.route_enrichment_service = route_enrichment_service
        self.schedule_builder = schedule_builder
        self.opening_hours_service = opening_hours_service
        self.max_route_attempts_per_step = (
            None
            if max_route_attempts_per_step is None
            else max(1, int(max_route_attempts_per_step))
        )

    async def retrieve_candidates(
        self,
        *,
        city_stay,
        requirement,
        top_k: int | None = None,
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

    async def plan(
        self,
        *,
        city_stay,
        requirement,
        top_k: int | None = None,
        narrate: bool = True,
    ) -> CityPlanningResult:
        attractions = await self.retrieve_candidates(
            city_stay=city_stay,
            requirement=requirement,
            top_k=top_k,
        )
        return await self._plan_from_attractions(
            city_stay=city_stay,
            requirement=requirement,
            attractions=attractions,
            narrate=narrate,
        )

    async def plan_from_candidates(
        self,
        *,
        city_stay,
        requirement,
        attractions: Sequence[HybridSearchResult],
        narrate: bool = True,
    ) -> CityPlanningResult:
        """Build a local-distance plan from an already bounded candidate pool.

        This is the recovery boundary used when route-aware selection cannot
        find a verified segment.  It deliberately does not retrieve a wider
        pool, so the hard-interest filter and the 20-item candidate limit are
        still respected during recovery.
        """

        return await self._plan_from_attractions(
            city_stay=city_stay,
            requirement=requirement,
            attractions=attractions,
            narrate=narrate,
        )

    async def plan_with_routes(
        self,
        *,
        city_stay,
        requirement,
        city_adcode: str,
        attractions: Sequence[HybridSearchResult],
        narrate: bool = True,
    ) -> RouteAwareCityPlanningResult:
        """Plan from enriched candidates while validating routes before selection."""

        if self.route_enrichment_service is None or self.schedule_builder is None:
            raise CityPlanningError("路线感知排程未完成服务配置")

        unique_attractions = await self._enrich_opening_hours(
            dedupe_attractions(list(attractions))
        )
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

        city_plan = dynamic_result.city_plan
        if narrate:
            city_plan = await self._apply_narration(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
                city_plan=city_plan,
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
        narrate: bool = True,
    ) -> CityPlanningResult:
        unique_attractions = await self._enrich_opening_hours(
            dedupe_attractions(list(attractions))
        )
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

        city_plan = dynamic_result.city_plan
        if narrate:
            city_plan = await self._apply_narration(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
                city_plan=city_plan,
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
        if self.planner is None:
            return city_plan
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
                opening_hours_weekly=item.opening_hours_weekly,
                opening_hours_today=item.opening_hours_today,
                opening_hours_status=item.opening_hours_status,
                tag_match_count=item.tag_match_count,
                semantic_score=item.preference_similarity,
                keyword_score=item.keyword_score,
                preference_score=item.preference_score,
            )
            for item in attractions
        ]

    async def _enrich_opening_hours(
        self,
        attractions: list[HybridSearchResult],
    ) -> list[HybridSearchResult]:
        if self.opening_hours_service is None or not attractions:
            return attractions
        try:
            enriched = await self.opening_hours_service.enrich_opening_hours(attractions)
        except Exception as exc:
            logger.warning(
                "高德营业时间补充失败，继续按未核实数据排程：error=%s",
                type(exc).__name__,
            )
            return [
                item.model_copy(update={"opening_hours_status": "unavailable"})
                for item in attractions
            ]
        enriched_by_id = {item.poi_id: item for item in enriched}
        return [
            enriched_by_id.get(
                item.poi_id,
                item.model_copy(update={"opening_hours_status": "unavailable"}),
            )
            for item in attractions
        ]


__all__ = ["CityPlanningService", "RouteAwareCityPlanningResult"]
