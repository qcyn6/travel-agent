import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.auth import CurrentUser, get_current_user
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
from backend.models.travel_plan import (
    AttractionReplacementPreview,
    AttractionReplacementRequest,
)
from backend.planner.attraction_replacement import (
    build_replacement_preview,
    list_replacement_options,
)
from backend.planner.runtime import build_travel_workflow
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


def _sse(event: str, data, *, event_id: int | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    event_id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{event_id_line}event: {event}\ndata: {payload}\n\n"


def _stream_headers(job_id: str | None = None) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if job_id:
        headers["X-Planning-Job-Id"] = job_id
    return headers


async def _dispatch_job(service, job_id: str) -> bool:
    dispatcher = getattr(service, "dispatch_job", None)
    if dispatcher is None:
        return False
    return bool(await dispatcher(job_id))


def _owned_repository(repository, user: CurrentUser | None):
    if user is None:
        return repository
    return repository.scoped(user.id)


@router.post("", response_model=ConversationCreated, status_code=201)
def create_conversation(
    repository: ConversationRepository = Depends(get_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> ConversationCreated:
    row = _owned_repository(repository, user).create()
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
    user: CurrentUser | None = Depends(get_current_user),
) -> list[ConversationSummary]:
    return [
        ConversationSummary.model_validate(row)
        for row in _owned_repository(repository, user).list_recent(limit=limit)
    ]


@router.get("/{conversation_id}", response_model=Conversation)
def get_conversation(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> Conversation:
    repository = _owned_repository(repository, user)
    service = _conversation_service(repository, CatalogRepository(session), settings)
    try:
        return service.get(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> None:
    if not _owned_repository(repository, user).delete(conversation_id):
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")


@router.get("/{conversation_id}/plans")
def list_conversation_plans(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_conversation_repository),
    plan_repository: TravelPlanRepository = Depends(get_travel_plan_repository),
    user: CurrentUser | None = Depends(get_current_user),
):
    repository = _owned_repository(repository, user)
    if repository.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")
    return plan_repository.list_for_session(conversation_id)


@router.get("/{conversation_id}/plans/replacement-options")
def get_attraction_replacement_options(
    conversation_id: str,
    day: int = Query(ge=1),
    current_poi_id: str = Query(min_length=1),
    repository: ConversationRepository = Depends(get_conversation_repository),
    plan_repository: TravelPlanRepository = Depends(get_travel_plan_repository),
    user: CurrentUser | None = Depends(get_current_user),
):
    repository = _owned_repository(repository, user)
    if repository.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")
    plans = plan_repository.list_for_session(conversation_id)
    if not plans:
        raise HTTPException(status_code=404, detail="当前会话没有已保存的旅行方案")
    return list_replacement_options(
        _saved_plan_response(plans[0]),
        day_number=day,
        current_poi_id=current_poi_id,
    )


@router.post(
    "/{conversation_id}/plans/attraction-replacement/preview",
    response_model=AttractionReplacementPreview,
)
async def preview_attraction_replacement(
    conversation_id: str,
    payload: AttractionReplacementRequest,
    repository: ConversationRepository = Depends(get_conversation_repository),
    plan_repository: TravelPlanRepository = Depends(get_travel_plan_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> AttractionReplacementPreview:
    return await _prepare_attraction_replacement(
        conversation_id,
        payload,
        repository=repository,
        plan_repository=plan_repository,
        settings=settings,
        session=session,
        user=user,
        save=False,
    )


@router.post(
    "/{conversation_id}/plans/attraction-replacement",
    response_model=AttractionReplacementPreview,
)
async def replace_attraction(
    conversation_id: str,
    payload: AttractionReplacementRequest,
    repository: ConversationRepository = Depends(get_conversation_repository),
    plan_repository: TravelPlanRepository = Depends(get_travel_plan_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> AttractionReplacementPreview:
    return await _prepare_attraction_replacement(
        conversation_id,
        payload,
        repository=repository,
        plan_repository=plan_repository,
        settings=settings,
        session=session,
        user=user,
        save=True,
    )


async def _prepare_attraction_replacement(
    conversation_id,
    payload,
    *,
    repository,
    plan_repository,
    settings,
    session,
    user,
    save: bool,
):
    repository = _owned_repository(repository, user)
    if repository.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前会话不存在或已失效")
    plans = plan_repository.list_for_session(conversation_id)
    if not plans:
        raise HTTPException(status_code=404, detail="当前会话没有已保存的旅行方案")
    try:
        workflow = build_travel_workflow(session, settings)
        preview = await build_replacement_preview(
            workflow,
            _saved_plan_response(plans[0]),
            payload,
            include_display_enrichment=save,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if save:
        plan_repository.save(conversation_id, preview.result)
    return preview


def _saved_plan_response(saved_plan):
    from backend.models.travel_plan import TravelPlanResponse

    return TravelPlanResponse(
        requirement=saved_plan.requirement,
        plan=saved_plan.plan,
    )


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: ChatMessageRequest,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> StreamingResponse:
    if payload.mode != "planning":
        raise HTTPException(
            status_code=409,
            detail="普通模式请使用 /api/v1/general-conversations；当前接口仅处理规划模式。",
        )
    repository = _owned_repository(repository, user)
    service = _conversation_service(repository, CatalogRepository(session), settings)
    graph_runner = getattr(service, "run_graph", None)
    if callable(graph_runner):
        if repository.get(conversation_id) is None:
            raise HTTPException(status_code=404, detail="当前会话不存在或已失效")

        async def graph_events() -> AsyncIterator[str]:
            try:
                conversation, reply, options, prepared = await graph_runner(
                    conversation_id,
                    payload.message,
                    mode=payload.mode,
                )
            except ConversationNotFoundError as exc:
                yield _sse("error", {"message": str(exc)})
                return

            if (
                prepared.intent == "planning"
                and not prepared.mode_boundary
                and not prepared.validation_error
            ):
                yield _sse(
                    "requirement_updated",
                    prepared.requirement.model_dump(mode="json"),
                )
            yield _sse("suggestions", {"options": options})
            yield _sse("message_delta", {"text": reply})
            requirement_ready = (
                conversation.requirement_ready
                if (
                    prepared.intent == "planning"
                    and not prepared.mode_boundary
                    and not prepared.validation_error
                )
                else False
            )
            yield _sse("message_done", {"requirement_ready": requirement_ready})
            if requirement_ready:
                yield _sse("requirement_ready", {"ready": True})

        return StreamingResponse(
            graph_events(),
            media_type="text/event-stream",
            headers=_stream_headers(),
        )
    try:
        prepared = await service.prepare_message(
            conversation_id,
            payload.message,
            mode=payload.mode,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        if prepared.intent == "planning" and not prepared.mode_boundary and not prepared.validation_error:
            yield _sse("requirement_updated", prepared.requirement.model_dump(mode="json"))
        planning_reply = None
        if (
            prepared.intent == "planning"
            and not prepared.mode_boundary
            and not prepared.validation_error
            and not prepared.destination_error
            and not prepared.should_invite_more
        ):
            planning_reply = await service.planning_reply(prepared)
        options = service.suggestions_for(
            prepared.missing,
            prepared.requirement,
            intent=prepared.intent,
        )
        if planning_reply is not None:
            options = service.suggestions_for_reply(prepared, planning_reply)
        if prepared.validation_error and prepared.intent == "planning":
            options = service.validation_suggestions(
                prepared.validation_error,
                prepared.requirement,
            )
        yield _sse(
            "suggestions",
            {"options": options},
        )
        chunks: list[str] = []
        if prepared.mode_boundary:
            reply = prepared.mode_boundary
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif prepared.validation_error:
            reply = prepared.validation_error
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif prepared.destination_error:
            reply = (
                f"{prepared.destination_error}。换一个已收录的国内省份或城市试试，"
                "也可以让我根据你的兴趣和偏好推荐一个省份。"
            )
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif prepared.should_invite_more:
            reply = service.more_details_reply(prepared.requirement)
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif prepared.intent == "general":
            try:
                async for chunk in service.responder.stream(
                    user_message=prepared.user_message,
                    **service.responder_inputs(prepared),
                    recent_messages=prepared.recent_messages,
                    conversation_mode="general",
                ):
                    chunks.append(chunk)
                    yield _sse("message_delta", {"text": chunk})
            except ConversationResponseError:
                if not chunks:
                    fallback = service._general_fallback_reply(
                        prepared.user_message,
                        prepared.destination_context,
                    )
                    chunks.append(fallback)
                    yield _sse("message_delta", {"text": fallback})
                else:
                    chunks.append("（回复连接中断）")
                    yield _sse("message_delta", {"text": "（回复连接中断）"})
        else:
            reply = planning_reply or service._planning_fallback(prepared)
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        reply = "".join(chunks).strip()
        conversation = service.finish_message(prepared, reply)
        requirement_ready = (
            conversation.requirement_ready
            if (
                prepared.intent == "planning"
                and not prepared.mode_boundary
                and not prepared.validation_error
            )
            else False
        )
        yield _sse("message_done", {"requirement_ready": requirement_ready})
        if requirement_ready:
            yield _sse("requirement_ready", {"ready": True})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_stream_headers())


@router.patch("/{conversation_id}/requirement", response_model=Conversation)
def update_requirement(
    conversation_id: str,
    patch: RequirementDraftPatch,
    repository: ConversationRepository = Depends(get_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> Conversation:
    repository = _owned_repository(repository, user)
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
    user: CurrentUser | None = Depends(get_current_user),
) -> RequirementConfirmation:
    repository = _owned_repository(repository, user)
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
    job_id: str | None = Query(default=None, max_length=36),
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
    repository: ConversationRepository = Depends(get_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> StreamingResponse:
    if user is not None and _owned_repository(repository, user).get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前旅行计划不存在或已失效")
    try:
        job = planning_service.prepare_conversation(conversation_id)
        # If the client sends a cursor belonging to an older retry, start the
        # newly-created job from its first event instead of silently skipping
        # its short sequence numbers.
        effective_after = after if job_id in (None, job.job_id) else 0
        queued_externally = await _dispatch_job(planning_service, job.job_id)
        if queued_externally:
            event_stream = planning_service.stream_job_events(
                job.job_id,
                after=effective_after,
                start_local_worker=False,
            )
        else:
            event_stream = planning_service.stream_job_events(
                job.job_id,
                after=effective_after,
            )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanningConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        async for event_id, event, data in event_stream:
            yield _sse(event, data, event_id=event_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers=_stream_headers(job.job_id),
    )


@router.get("/{conversation_id}/plan/events")
async def resume_plan_events(
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    job_id: str | None = Query(default=None, max_length=36),
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
    repository: ConversationRepository = Depends(get_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> StreamingResponse:
    if user is not None and _owned_repository(repository, user).get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="当前旅行计划不存在或已失效")
    try:
        latest_job_id = planning_service.latest_job_id(conversation_id)
        if latest_job_id is None:
            raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
        effective_after = after if job_id in (None, latest_job_id) else 0
        queued_externally = await _dispatch_job(planning_service, latest_job_id)
        if queued_externally:
            event_stream = planning_service.stream_job_events(
                latest_job_id,
                after=effective_after,
                start_local_worker=False,
            )
        else:
            event_stream = planning_service.stream_job_events(
                latest_job_id,
                after=effective_after,
            )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        async for event_id, event, data in event_stream:
            yield _sse(event, data, event_id=event_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers=_stream_headers(latest_job_id),
    )
