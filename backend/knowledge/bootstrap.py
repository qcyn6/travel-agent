import asyncio
import logging
from inspect import isawaitable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KnowledgeBootstrapResult(BaseModel):
    city: str
    ready_count: int = Field(ge=0)
    attempted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    partial: bool = False


class KnowledgeBootstrapError(RuntimeError):
    pass


class CityKnowledgeBootstrapService:
    def __init__(
        self,
        *,
        poi_service,
        attraction_repository,
        enrichment_service,
        max_concurrency: int = 3,
        prefer_external: bool = False,
    ):
        self.poi_service = poi_service
        self.attraction_repository = attraction_repository
        self.enrichment_service = enrichment_service
        self.max_concurrency = max(1, int(max_concurrency))
        self.prefer_external = bool(prefer_external)

    async def ensure(
        self,
        *,
        city,
        target_ready: int = 3,
        max_attempts: int = 12,
        allow_partial: bool = False,
        progress=None,
    ) -> KnowledgeBootstrapResult:
        external_attempted = False
        if self.prefer_external and self.poi_service is not None:
            # A live provider is deliberately attempted even when the local
            # catalog already has enough rows. This keeps newly generated
            # plans fresh while the provider service itself owns the DB
            # fallback when MCP is unavailable.
            external_attempted = True
            try:
                await self.poi_service.search_and_store(
                    city_id=city.id,
                    city_name=city.name,
                    limit=30,
                )
            except Exception as exc:
                logger.warning(
                    "%s 高德 MCP 景点搜索失败，继续使用本地资料：%s",
                    city.name,
                    type(exc).__name__,
                )

        ready = self.attraction_repository.count_ready_for_city(city.id)
        if ready >= target_ready:
            await self._report_progress(
                progress,
                ready=ready,
                target_ready=target_ready,
                attempted=0,
                candidate_count=0,
                failed=0,
            )
            return KnowledgeBootstrapResult(city=city.name, ready_count=ready, attempted_count=0, failed_count=0)

        all_rows = self.attraction_repository.list_for_city(city.id, limit=1)
        if not all_rows and self.poi_service is not None and not external_attempted:
            await self.poi_service.search_and_store(
                city_id=city.id,
                city_name=city.name,
                limit=30,
            )

        candidates = self.attraction_repository.list_for_city(
            city.id,
            statuses={"pending", "failed"},
            limit=max_attempts * 3,
        )
        candidates.sort(key=self._candidate_priority)
        candidates = candidates[:max_attempts]
        attempted = 0
        failed = 0
        await self._report_progress(
            progress,
            ready=ready,
            target_ready=target_ready,
            attempted=attempted,
            candidate_count=len(candidates),
            failed=failed,
        )
        if self.enrichment_service is None:
            # The production workflow owns no automatic attraction data
            # acquisition. Missing rows must be prepared and marked ready in
            # MySQL before planning can use them.
            actual_ready = self.attraction_repository.count_ready_for_city(city.id)
            if allow_partial and actual_ready > 0:
                logger.warning(
                    "%s 本地景点资料不足（%s/%s），按现有资料继续规划",
                    city.name,
                    actual_ready,
                    target_ready,
                )
                return KnowledgeBootstrapResult(
                    city=city.name,
                    ready_count=actual_ready,
                    attempted_count=0,
                    failed_count=0,
                    partial=True,
                )
            if self.prefer_external:
                message = (
                    f"{city.name} 未能从高德 MCP 或本地数据库获得足够的可用景点知识："
                    f"当前 {actual_ready} 条，目标为 {target_ready} 条"
                )
            else:
                message = (
                    f"{city.name} 本地数据库仅有 {actual_ready} 条可用景点知识，"
                    f"目标为 {target_ready} 条；数据库唯一来源模式不会自动联网补充"
                )
            raise KnowledgeBootstrapError(message)
        while ready < target_ready and attempted < len(candidates):
            # Keep a full batch in flight even when only one more success is
            # required. External source lookup is the slowest and least reliable
            # stage, so racing a few verified candidates is faster than waiting
            # for failed candidates one by one.
            batch_size = min(self.max_concurrency, len(candidates) - attempted)
            batch = candidates[attempted : attempted + batch_size]
            attempted += len(batch)
            outcomes = await asyncio.gather(
                *(self._enrich_candidate(city, attraction) for attraction in batch)
            )
            succeeded = sum(outcomes)
            ready += succeeded
            failed += len(outcomes) - succeeded
            await self._report_progress(
                progress,
                ready=ready,
                target_ready=target_ready,
                attempted=attempted,
                candidate_count=len(candidates),
                failed=failed,
            )

        actual_ready = self.attraction_repository.count_ready_for_city(city.id)
        if actual_ready < target_ready:
            if allow_partial and actual_ready > 0:
                logger.warning(
                    "%s 仅准备好 %s 条景点知识，目标为 %s，将使用现有资料继续规划",
                    city.name,
                    actual_ready,
                    target_ready,
                )
                return KnowledgeBootstrapResult(
                    city=city.name,
                    ready_count=actual_ready,
                    attempted_count=attempted,
                    failed_count=failed,
                    partial=True,
                )
            raise KnowledgeBootstrapError(
                f"{city.name} 仅准备好 {actual_ready} 条景点知识，目标为 {target_ready} 条"
            )
        return KnowledgeBootstrapResult(
            city=city.name,
            ready_count=actual_ready,
            attempted_count=attempted,
            failed_count=failed,
        )

    @staticmethod
    async def _report_progress(
        progress,
        *,
        ready: int,
        target_ready: int,
        attempted: int,
        candidate_count: int,
        failed: int,
    ) -> None:
        if progress is None:
            return
        result = progress(ready, target_ready, attempted, candidate_count, failed)
        if isawaitable(result):
            await result

    @staticmethod
    def _candidate_priority(attraction) -> tuple[int, str]:
        """Keep compatibility callers deterministic without provider fields."""

        return 0, str(getattr(attraction, "poi_id", ""))

    async def _enrich_candidate(self, city, attraction) -> int:
        if self.enrichment_service is None:
            return 0
        try:
            await self.enrichment_service.enrich(
                poi_id=attraction.poi_id,
                name=attraction.name,
                city=city.name,
                category=attraction.category,
            )
        except Exception as exc:
            logger.warning(
                "景点知识增强失败，继续尝试下一候选：%s (%s)",
                attraction.poi_id,
                type(exc).__name__,
            )
            return 0
        return 1
