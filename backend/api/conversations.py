import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.config import Settings, get_settings
from backend.conversation import (
    ConversationNotFoundError,
    ConversationRepository,
    ConversationService,
    RequirementNotReadyError,
    get_conversation_repository,
)
from backend.conversation.responder import DeepSeekConversationResponder
from backend.database import get_db
from backend.destination import DestinationPlanningError, DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.knowledge import KnowledgeBootstrapError
from backend.models.conversation import (
    ChatMessageRequest,
    Conversation,
    ConversationCreated,
    RequirementConfirmation,
    RequirementDraft,
    RequirementDraftPatch,
)
from backend.planner import DestinationChoiceRequired, build_travel_workflow
from backend.requirements.runtime import build_requirement_parser


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
        conversation, reply, suggestions = await service.handle_message(
            conversation_id,
            payload.message,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        yield _sse("requirement_updated", conversation.requirement.model_dump(mode="json"))
        yield _sse("suggestions", {"options": suggestions})
        for index in range(0, len(reply), 4):
            yield _sse("message_delta", {"text": reply[index : index + 4]})
            await asyncio.sleep(0.015)
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
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> StreamingResponse:
    row = repository.get(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")
    if not row.requirement_confirmed:
        raise HTTPException(status_code=409, detail="请先确认旅行需求")
    requirement = RequirementDraft.model_validate(row.requirement_draft or {})
    workflow = build_travel_workflow(session, settings)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def progress(step: str, label: str, status: str) -> None:
            await queue.put(("planning_progress", {"step": step, "label": label, "status": status}))

        async def run_plan() -> None:
            repository.set_status(row, "planning")
            try:
                result = await workflow.run_requirement(requirement, progress=progress)
                repository.set_status(row, "completed")
                await queue.put(("plan_completed", result.model_dump(mode="json")))
            except DestinationChoiceRequired as exc:
                repository.set_status(row, "confirmed")
                await queue.put(
                    (
                        "destination_choice_required",
                        {
                            "requirement": exc.requirement.model_dump(mode="json"),
                            "options": exc.options.model_dump(mode="json"),
                        },
                    )
                )
            except (ValueError, DestinationPlanningError) as exc:
                repository.set_status(row, "confirmed")
                await queue.put(("planning_error", {"message": str(exc)}))
            except KnowledgeBootstrapError as exc:
                repository.set_status(row, "confirmed")
                await queue.put(("planning_error", {"message": str(exc)}))
            except Exception:
                repository.set_status(row, "confirmed")
                await queue.put(("planning_error", {"message": "旅游方案生成失败，请稍后重试"}))
            finally:
                await queue.put(("stream_done", {}))

        task = asyncio.create_task(run_plan())
        try:
            while True:
                event, data = await queue.get()
                if event == "stream_done":
                    break
                yield _sse(event, data)
        finally:
            await task

    return StreamingResponse(events(), media_type="text/event-stream", headers=_stream_headers())
