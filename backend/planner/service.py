from backend.models.itinerary import CityPlanningResult
from backend.models.retrieval import AttractionDocument
from backend.planner.city import CityPlanningError, deterministic_city_plan


class CityPlanningService:
    def __init__(self, *, retrieval_service, planner):
        self.retrieval_service = retrieval_service
        self.planner = planner

    async def plan(self, *, city_stay, requirement, top_k: int = 10) -> CityPlanningResult:
        attractions = await self.retrieval_service.search(
            city=city_stay.city,
            interests=requirement.interests,
            preference_text=requirement.preference_text,
            top_k=top_k,
        )
        candidates = [
            AttractionDocument(
                poi_id=item.poi_id,
                name=item.name,
                city=item.city,
                category=item.category,
                longitude=item.longitude,
                latitude=item.latitude,
                description=item.description,
                source_url=item.source_url,
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
        return CityPlanningResult(
            city_stay=city_stay,
            attractions=attractions,
            city_plan=city_plan,
        )
