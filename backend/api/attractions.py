from fastapi import APIRouter, Depends, HTTPException, Query

from backend.attractions import AttractionRepository, get_attraction_repository
from backend.config import Settings, get_settings
from backend.destination.repository import CatalogRepository, get_catalog_repository
from backend.models.attraction import AttractionPOI
from backend.poi import AmapAPIError, AmapAttractionClient, AmapConfigurationError
from backend.services import AttractionService


router = APIRouter(prefix="/api/v1/cities", tags=["attractions"])


@router.get("/{city}/attractions", response_model=list[AttractionPOI])
async def list_attractions(
    city: str,
    limit: int = Query(default=30, ge=1, le=50),
    repository: CatalogRepository = Depends(get_catalog_repository),
    attraction_repository: AttractionRepository = Depends(get_attraction_repository),
    settings: Settings = Depends(get_settings),
) -> list[AttractionPOI]:
    city_row = repository.find_city(city)
    if city_row is None:
        raise HTTPException(status_code=404, detail=f"暂不支持或无法识别城市：{city}")

    try:
        client = AmapAttractionClient(
            settings.amap_api_key,
            base_url=settings.amap_base_url,
            timeout_seconds=settings.amap_timeout_seconds,
            max_retries=settings.amap_max_retries,
        )
        return await AttractionService(client, attraction_repository).search_and_store(
            city_id=city_row.id,
            city_name=city_row.name,
            limit=limit,
        )
    except AmapConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AmapAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="景点数据写入 MySQL 失败") from exc
