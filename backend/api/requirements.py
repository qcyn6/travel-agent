from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.destination.repository import CatalogRepository, get_catalog_repository
from backend.destination.resolver import DestinationResolutionError, DestinationResolver, ResolvedDestination
from backend.config import Settings, get_settings
from backend.requirements.runtime import build_requirement_parser


router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])


class ParseRequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


@router.post("/resolve", response_model=ResolvedDestination)
async def parse_and_resolve(
    payload: ParseRequirementRequest,
    repository: CatalogRepository = Depends(get_catalog_repository),
    settings: Settings = Depends(get_settings),
) -> ResolvedDestination:
    try:
        requirement = await build_requirement_parser(settings).parse(payload.text)
        return DestinationResolver(repository).resolve(requirement)
    except (ValueError, DestinationResolutionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
