from fastapi import APIRouter, Depends, HTTPException, Response

from backend.application import PlanningApplicationService
from backend.api.dependencies import get_planning_application_service
from backend.destination import DestinationPlanningError
from backend.knowledge import KnowledgeBootstrapError
from backend.models.travel_plan import (
    DestinationChoiceResponse,
    TravelPlanRequest,
    TravelPlanResponse,
)


router = APIRouter(prefix="/api/v1/travel", tags=["travel"])


@router.post(
    "/plan",
    response_model=TravelPlanResponse | DestinationChoiceResponse,
)
async def create_travel_plan(
    request: TravelPlanRequest,
    response: Response,
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
) -> TravelPlanResponse | DestinationChoiceResponse:
    try:
        execution = await planning_service.run_standalone(
            request.text,
            travel_scope=request.travel_scope,
        )
        response.headers["X-Conversation-Id"] = execution.conversation_id
        return execution.response
    except (ValueError, DestinationPlanningError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KnowledgeBootstrapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="旅游方案生成失败") from exc
