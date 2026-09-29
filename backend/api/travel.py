from fastapi import APIRouter, Depends, HTTPException, Response

from backend.application import PlanningApplicationService
from backend.api.dependencies import get_planning_application_service
from backend.auth import CurrentUser, get_current_user
from backend.destination import DestinationPlanningError
from backend.knowledge import KnowledgeBootstrapError
from backend.models.travel_plan import (
    DestinationChoiceResponse,
    TravelPlanRequest,
    TravelPlanResponse,
)


router = APIRouter(prefix="/api/v1/travel", tags=["travel"])


def _conversation_error_headers(error: Exception) -> dict[str, str] | None:
    conversation_id = getattr(error, "conversation_id", None)
    if isinstance(conversation_id, str) and conversation_id:
        return {"X-Conversation-Id": conversation_id}
    return None


@router.post(
    "/plan",
    response_model=TravelPlanResponse | DestinationChoiceResponse,
)
async def create_travel_plan(
    request: TravelPlanRequest,
    response: Response,
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
    user: CurrentUser | None = Depends(get_current_user),
) -> TravelPlanResponse | DestinationChoiceResponse:
    try:
        options = {"travel_scope": request.travel_scope}
        if user is not None:
            options["user_id"] = user.id
        execution = await planning_service.run_standalone(request.text, **options)
        response.headers["X-Conversation-Id"] = execution.conversation_id
        return execution.response
    except (ValueError, DestinationPlanningError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers=_conversation_error_headers(exc),
        ) from exc
    except KnowledgeBootstrapError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers=_conversation_error_headers(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="旅游方案生成失败",
            headers=_conversation_error_headers(exc),
        ) from exc
