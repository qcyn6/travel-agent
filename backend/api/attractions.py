import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.attractions import AttractionRepository, get_attraction_repository
from backend.config import Settings, get_settings
from backend.destination.repository import CatalogRepository, get_catalog_repository
from backend.models.attraction import AttractionPOI
from backend.poi import AmapMcpAttractionClient, DatabaseAttractionProvider


router = APIRouter(prefix="/api/v1/cities", tags=["attractions"])
logger = logging.getLogger(__name__)


@router.get("/{city}/attractions", response_model=list[AttractionPOI])
async def list_attractions(
    city: str,
    limit: int = Query(default=30, ge=1, le=50),
    interests: list[str] = Query(
        default=[],
        description="按景点标签匹配用户兴趣，可重复传入，例如 interests=自然&interests=摄影",
    ),
    use_amap: bool = Query(
        default=True,
        description="是否为本地筛选出的景点补充高德详情；传 false 可只读本地数据库",
    ),
    repository: CatalogRepository = Depends(get_catalog_repository),
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
    settings: Settings = Depends(get_settings),
) -> list[AttractionPOI]:
    city_row = repository.find_city(city)
    if city_row is None:
        raise HTTPException(status_code=404, detail=f"暂不支持或无法识别城市：{city}")

    normalized_interests = [
        item.strip()
        for value in interests
        for item in value.split(",")
        if item.strip()
    ]
    fallback = DatabaseAttractionProvider(
        attraction_repository,
        repository,
        interests=normalized_interests,
    )
    if not use_amap:
        return await fallback.search_attractions(city_row.name, limit=limit)

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
            cache_ttl_seconds=getattr(settings, "redis_poi_ttl_seconds", 86400),
        )
    except Exception as exc:
        # A missing key or an unavailable MCP adapter should not make the
        # locally selected attraction list unusable.
        logger.warning(
            "高德 MCP 景点适配器不可用，改用本地数据库：city=%s error=%s",
            city_row.name,
            type(exc).__name__,
        )
        try:
            return await fallback.search_attractions(city_row.name, limit=limit)
        except Exception as fallback_exc:
            raise HTTPException(status_code=503, detail="景点数据不可用") from fallback_exc

    try:
        local_attractions = await fallback.search_attractions(city_row.name, limit=limit)
        if not local_attractions:
            return []
        enriched = await client.enrich_attractions(local_attractions)
        attraction_repository.upsert_many(city_row.id, enriched)
        return enriched
    except Exception as exc:
        logger.warning(
            "高德详情补充或景点数据写入失败，保留本地筛选结果：city=%s error=%s",
            city_row.name,
            type(exc).__name__,
        )
        try:
            return await fallback.search_attractions(city_row.name, limit=limit)
        except Exception as fallback_exc:
            raise HTTPException(status_code=503, detail="景点数据不可用") from fallback_exc
