from backend.attractions.dedupe import dedupe_attractions
from backend.models.itinerary import CityPlanningResult
from backend.models.planning import PlanningCandidate
from backend.planner.city import (
    CityPlanningError,
    deduplicate_city_plan,
    deterministic_city_plan,
)


class CityPlanningService:
    def __init__(self, *, retrieval_service, planner):
        self.retrieval_service = retrieval_service
        self.planner = planner

    async def plan(self, *, city_stay, requirement, top_k: int = 20) -> CityPlanningResult:
        attractions = dedupe_attractions(await self.retrieval_service.search(
            city=city_stay.city,
            interests=requirement.interests,
            preference_text=requirement.preference_text,
            top_k=top_k,
        ))
        candidates = [
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
        try:
            city_plan = await self.planner.plan(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
            )
        except CityPlanningError:
            city_plan = deterministic_city_plan(
                city_stay=city_stay,
                requirement=requirement,
                candidates=candidates,
            )
        city_plan = deduplicate_city_plan(plan=city_plan, candidates=candidates)
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=attractions,
            city_plan=city_plan,
        )
