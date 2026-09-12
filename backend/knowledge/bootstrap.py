from pydantic import BaseModel, Field


class KnowledgeBootstrapResult(BaseModel):
    city: str
    ready_count: int = Field(ge=0)
    attempted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)


class KnowledgeBootstrapError(RuntimeError):
    pass


class CityKnowledgeBootstrapService:
    def __init__(
        self,
        *,
        poi_service,
        attraction_repository,
        enrichment_service,
    ):
        self.poi_service = poi_service
        self.attraction_repository = attraction_repository
        self.enrichment_service = enrichment_service

    async def ensure(
        self,
        *,
        city,
        target_ready: int = 3,
        max_attempts: int = 12,
    ) -> KnowledgeBootstrapResult:
        ready = self.attraction_repository.count_ready_for_city(city.id)
        if ready >= target_ready:
            return KnowledgeBootstrapResult(city=city.name, ready_count=ready, attempted_count=0, failed_count=0)

        all_rows = self.attraction_repository.list_for_city(city.id, limit=1)
        if not all_rows:
            await self.poi_service.search_and_store(
                city_id=city.id,
                city_name=city.name,
                limit=30,
            )

        candidates = self.attraction_repository.list_for_city(
            city.id,
            statuses={"pending"},
            limit=max_attempts,
        )
        attempted = 0
        failed = 0
        for attraction in candidates:
            if ready >= target_ready:
                break
            attempted += 1
            try:
                await self.enrichment_service.enrich(
                    poi_id=attraction.poi_id,
                    name=attraction.name,
                    city=city.name,
                    category=attraction.category,
                )
            except Exception:
                failed += 1
                continue
            ready += 1

        actual_ready = self.attraction_repository.count_ready_for_city(city.id)
        if actual_ready < target_ready:
            raise KnowledgeBootstrapError(
                f"{city.name} 仅准备好 {actual_ready} 条景点知识，目标为 {target_ready} 条"
            )
        return KnowledgeBootstrapResult(
            city=city.name,
            ready_count=actual_ready,
            attempted_count=attempted,
            failed_count=failed,
        )
