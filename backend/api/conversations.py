import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.application import (
    PlanningApplicationService,
    PlanningConflictError,
)
from backend.api.dependencies import get_planning_application_service
from backend.config import Settings, get_settings
from backend.conversation import (
    ConversationNotFoundError,
    ConversationRepository,
    ConversationService,
    RequirementNotReadyError,
    get_conversation_repository,
)
from backend.conversation.responder import ConversationResponseError, DeepSeekConversationResponder
from backend.database import get_db
from backend.destination import DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.models.conversation import (
    ChatMessageRequest,
    Conversation,
    ConversationCreated,
    RequirementConfirmation,
    RequirementDraft,
    RequirementDraftPatch,
    ConversationSummary,
)
from backend.requirements.runtime import build_requirement_parser
from backend.travel_plans import TravelPlanRepository, get_travel_plan_repository


router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _conversation_service(repository, catalog, settings) -> ConversationService:
    return ConversationService(
        repository=repository,
        parser=build_requirement_parser(settings),
        resolver=DestinationResolver(catalog),
        responder=DeepSeekConversationResponder(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@router.post("", response_model=ConversationCreated, status_code=201)
def create_conversation(
    repository: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationCreated:
    row = repository.create()
    return ConversationCreated(
        id=row.id,
        title=row.title,
        status=row.status,
        requirement=RequirementDraft.model_validate(row.requirement_draft),
    )


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    repository: ConversationRepository = Depends(get_conversation_repository),
) -> list[ConversationSummary]:
    return [ConversationSummary.model_validate(row) for row in repository.list_recent(limit=limit)]


@router.get("/{conversation_id}", response_model=Conversation)
def get_conversation(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> Conversation:
    service = _conversation_service(repository, CatalogRepository(session), settings)
    try:
        return service.get(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
) -> None:
    if not repository.delete(conversation_id):
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")


@router.get("/{conversation_id}/plans")
def list_conversation_plans(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    plan_repository: TravelPlanRepository = Depends(get_travel_plan_repository),
):
    if repository.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")
    return plan_repository.list_for_session(conversation_id)


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: ChatMessageRequest,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> StreamingResponse:
    service = _conversation_service(repository, CatalogRepository(session), settings)
    try:
        prepared = await service.prepare_message(conversation_id, payload.message)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        yield _sse("requirement_updated", prepared.requirement.model_dump(mode="json"))
        yield _sse("suggestions", {"options": service.suggestions_for(prepared.missing)})
        chunks: list[str] = []
        if prepared.destination_error:
            reply = f"{prepared.destination_error}。当前 MVP 支持浙江省、云南省和陕西省，你可以从这些地区中选择。"
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif not (prepared.requirement.province or prepared.requirement.cities):
            reply = service._fallback_reply(
                prepared.requirement,
                prepared.missing,
                prepared.destination_context,
            )
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        else:
            try:
                async for chunk in service.responder.stream(
                    user_message=prepared.user_message,
                    requirement=prepared.requirement,
                    missing_fields=prepared.missing,
                    destination_context=prepared.destination_context,
                    recent_messages=prepared.recent_messages,
                ):
                    chunks.append(chunk)
                    yield _sse("message_delta", {"text": chunk})
            except ConversationResponseError:
                if not chunks:
                    fallback = service._fallback_reply(
                        prepared.requirement,
                        prepared.missing,
                        prepared.destination_context,
                    )
                    chunks.append(fallback)
                    yield _sse("message_delta", {"text": fallback})
                else:
                    chunks.append("（回复连接中断）")
                    yield _sse("message_delta", {"text": "（回复连接中断）"})
        reply = "".join(chunks).strip()
        conversation = service.finish_message(prepared, reply)
        yield _sse("message_done", {"requirement_ready": conversation.requirement_ready})
        if conversation.requirement_ready:
            yield _sse("requirement_ready", {"ready": True})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_stream_headers())


@router.patch("/{conversation_id}/requirement", response_model=Conversation)
def update_requirement(
    conversation_id: str,
    patch: RequirementDraftPatch,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> Conversation:
    service = _conversation_service(repository, CatalogRepository(session), settings)
    try:
        return service.update_requirement(
            conversation_id,
            patch.model_dump(exclude_unset=True),
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{conversation_id}/requirement/confirm", response_model=RequirementConfirmation)
def confirm_requirement(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> RequirementConfirmation:
    service = _conversation_service(repository, CatalogRepository(session), settings)
    try:
        conversation = service.confirm(conversation_id)
        return RequirementConfirmation(
            conversation_id=conversation.id,
            requirement=conversation.requirement,
            confirmed=True,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RequirementNotReadyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{conversation_id}/plan")
async def plan_conversation(
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
) -> StreamingResponse:
    try:
        job = planning_service.prepare_conversation(conversation_id)
        event_stream = planning_service.stream_job_events(job.job_id, after=after)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanningConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        async for event, data in event_stream:
            yield _sse(event, data)

    return StreamingResponse(events(), media_type="text/event-stream", headers=_stream_headers())


@router.get("/{conversation_id}/plan/events")
async def resume_plan_events(
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
) -> StreamingResponse:
    try:
        job_id = planning_service.latest_job_id(conversation_id)
        if job_id is None:
            raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
        event_stream = planning_service.stream_job_events(job_id, after=after)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        async for event, data in event_stream:
            yield _sse(event, data)

    return StreamingResponse(events(), media_type="text/event-stream", headers=_stream_headers())
