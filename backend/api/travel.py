from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database import get_db
from backend.destination import DestinationPlanningError
from backend.knowledge import KnowledgeBootstrapError
from backend.models.travel_plan import (
    DestinationChoiceResponse,
    TravelPlanRequest,
    TravelPlanResponse,
)
from backend.planner import DestinationChoiceRequired, build_travel_workflow


router = APIRouter(prefix="/api/v1/travel", tags=["travel"])


@router.post(
    "/plan",
    response_model=TravelPlanResponse | DestinationChoiceResponse,
)
async def create_travel_plan(
    request: TravelPlanRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TravelPlanResponse | DestinationChoiceResponse:
    workflow = build_travel_workflow(session, settings)
    try:
        return await workflow.run(request.text, travel_scope=request.travel_scope)
    except DestinationChoiceRequired as exc:
        return DestinationChoiceResponse(
            requirement=exc.requirement,
            destination_options=exc.options,
        )
    except (ValueError, DestinationPlanningError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KnowledgeBootstrapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="旅游方案生成失败") from exc
