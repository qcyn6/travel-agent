"""Runtime wiring for post-retrieval Amap attraction enrichment."""

from backend.poi.amap_mcp import AmapMcpAttractionClient
from backend.poi.amap import AmapConfigurationError
from backend.poi.enrichment import AmapMcpCandidateEnrichmentService


def build_candidate_enrichment_service(
    *,
    attraction_repository,
    catalog_repository,
    settings,
    cache=None,
):
    """Build an optional Amap service used after local candidate selection."""

    try:
        client = AmapMcpAttractionClient(
            getattr(settings, "amap_api_key", None),
            endpoint=getattr(settings, "amap_mcp_url", "https://mcp.amap.com/mcp"),
            timeout_seconds=getattr(settings, "amap_mcp_timeout_seconds", 15.0),
            sse_read_timeout_seconds=getattr(
                settings,
                "amap_mcp_sse_read_timeout_seconds",
                20.0,
            ),
            detail_concurrency=getattr(settings, "amap_mcp_detail_concurrency", 5),
            cache=cache,
            cache_ttl_seconds=getattr(settings, "redis_poi_ttl_seconds", 86400),
        )
    except AmapConfigurationError:
        return None

    return AmapMcpCandidateEnrichmentService(
        client=client,
        attraction_repository=attraction_repository,
        catalog_repository=catalog_repository,
    )


__all__ = ["build_candidate_enrichment_service"]
