"""Enrich locally selected attractions with live Amap facts."""

import logging
from collections.abc import Sequence

from backend.models.attraction import AttractionPOI
from backend.models.retrieval import HybridSearchResult


logger = logging.getLogger(__name__)


class AmapMcpCandidateEnrichmentService:
    """Keep local retrieval authoritative and enrich only its candidates."""

    def __init__(self, *, client, attraction_repository, catalog_repository):
        self.client = client
        self.attraction_repository = attraction_repository
        self.catalog_repository = catalog_repository

    async def enrich(
        self,
        *,
        city: str,
        candidates: Sequence[HybridSearchResult],
    ) -> list[HybridSearchResult]:
        selected = list(candidates)
        if not selected:
            return []

        local_items = [
            AttractionPOI(
                poi_id=item.poi_id,
                name=item.name,
                city=item.city,
                category=item.category,
                tags=list(item.interest_tags),
                description=item.description,
                visit_duration_minutes=item.visit_duration_minutes,
                longitude=item.longitude,
                latitude=item.latitude,
            )
            for item in selected
        ]
        try:
            enriched = await self.client.enrich_attractions(local_items)
        except Exception as exc:
            # The candidate set has already passed local retrieval. Amap is an
            # optional factual enrichment step and must not make planning fail.
            logger.warning(
                "高德 MCP 详情补充失败，保留本地筛选结果：city=%s error=%s",
                city,
                type(exc).__name__,
            )
            return selected

        city_row = self.catalog_repository.find_city(city)
        if city_row is not None and enriched:
            try:
                # The upsert updates only factual provider fields for an
                # existing row; local tags, descriptions and visit durations
                # remain authoritative in the repository.
                self.attraction_repository.upsert_many(city_row.id, enriched)
            except Exception as exc:
                logger.warning(
                    "高德详情无法写回本地景点表，继续使用本次结果：city=%s error=%s",
                    city,
                    type(exc).__name__,
                )

        enriched_by_id = {item.poi_id: item for item in enriched}
        return [
            item.model_copy(
                update={
                    "category": enriched_by_id.get(item.poi_id, item).category,
                    "longitude": enriched_by_id.get(item.poi_id, item).longitude,
                    "latitude": enriched_by_id.get(item.poi_id, item).latitude,
                }
            )
            for item in selected
        ]


__all__ = ["AmapMcpCandidateEnrichmentService"]
