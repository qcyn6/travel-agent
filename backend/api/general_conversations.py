import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.auth import CurrentUser, get_current_user
from backend.config import Settings, get_settings
from backend.conversation import ConversationNotFoundError
from backend.conversation.general_repository import (
    GeneralConversationRepository,
    get_general_conversation_repository,
)
from backend.conversation.langchain_general import (
    LangChainGeneralAgent,
    LangChainGeneralTravelTools,
    LangChainGuideRetriever,
)
from backend.conversation.general_service import GeneralConversationService
from backend.conversation.general_tools import build_general_travel_tools
from backend.conversation.responder import ConversationResponseError
from backend.database import get_db
from backend.models.conversation import (
    GeneralChatCardUpdateRequest,
    GeneralChatCardUpdateResponse,
    ConversationSummary,
    GeneralChatMessageRequest,
    GeneralConversation,
)
from backend.rag.runtime import build_guide_retrieval_service


router = APIRouter(prefix="/api/v1/general-conversations", tags=["general-conversations"])


def _owned_repository(repository, user: CurrentUser | None):
    if user is None:
        return repository
    return repository.scoped(user.id)


def _general_service(repository, session, settings: Settings) -> GeneralConversationService:
    guide_service = build_guide_retrieval_service(settings)
    guide_retriever = LangChainGuideRetriever(service=guide_service)
    return GeneralConversationService(
        repository=repository,
        knowledge_retriever=guide_retriever,
        travel_tools=LangChainGeneralTravelTools(build_general_travel_tools(session, settings)),
    )


def _general_responder(settings: Settings) -> LangChainGeneralAgent:
    return LangChainGeneralAgent(
        settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        use_responses_api=settings.llm_provider.strip().lower() == "deepseek",
    )


async def _prepare_general_turn(service, conversation_id: str, message: str):
    """Persist conversation context; the ReAct Agent makes turn decisions."""
    return await service.prepare_message(conversation_id, message)


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("", response_model=GeneralConversation, status_code=201)
def create_general_conversation(
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> GeneralConversation:
    repository = _owned_repository(repository, user)
    return repository.to_model(repository.create())


@router.get("", response_model=list[ConversationSummary])
def list_general_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> list[ConversationSummary]:
    repository = _owned_repository(repository, user)
    return [
        ConversationSummary(
            id=row.id,
            title=row.title,
            status="active",
            mode="general",
            updated_at=row.updated_at,
        )
        for row in repository.list_recent(limit=limit)
    ]


@router.get("/{conversation_id}", response_model=GeneralConversation)
def get_general_conversation(
    conversation_id: str,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> GeneralConversation:
    repository = _owned_repository(repository, user)
    row = repository.get(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="当前普通对话不存在或已失效")
    return repository.to_model(row)


@router.delete("/{conversation_id}", status_code=204)
def delete_general_conversation(
    conversation_id: str,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> None:
    if not _owned_repository(repository, user).delete(conversation_id):
        raise HTTPException(status_code=404, detail="当前普通对话不存在或已失效")


@router.patch(
    "/{conversation_id}/messages/{sequence_number}/card",
    response_model=GeneralChatCardUpdateResponse,
)
def update_general_message_card(
    conversation_id: str,
    sequence_number: int,
    payload: GeneralChatCardUpdateRequest,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    user: CurrentUser | None = Depends(get_current_user),
) -> GeneralChatCardUpdateResponse:
    repository = _owned_repository(repository, user)
    row = repository.get(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="当前普通对话不存在或已失效")
    if not repository.update_message_card(
        row,
        sequence_number=sequence_number,
        card=payload.card.model_dump(mode="json"),
    ):
        raise HTTPException(status_code=404, detail="未找到对应的助手消息")
    updated = repository.get(conversation_id)
    message = (updated.messages or [])[sequence_number - 1]
    return GeneralChatCardUpdateResponse(content=str(message.get("content", "")))


@router.post("/{conversation_id}/messages")
async def send_general_message(
    conversation_id: str,
    payload: GeneralChatMessageRequest,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
    user: CurrentUser | None = Depends(get_current_user),
) -> StreamingResponse:
    repository = _owned_repository(repository, user)
    service = _general_service(repository, session, settings)
    responder = _general_responder(settings)
    configure_tools = getattr(responder, "configure_tools", None)
    if callable(configure_tools):
        configure_tools(
            travel_tools=getattr(service, "travel_tools", None),
            guide_retriever=getattr(service, "knowledge_retriever", None),
        )
    try:
        prepared = await _prepare_general_turn(service, conversation_id, payload.message)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    refresh_memory = getattr(service, "refresh_memory", None)
    summarize_memory = getattr(responder, "summarize_memory", None)
    if callable(refresh_memory) and callable(summarize_memory):
        await refresh_memory(prepared, summarize_memory)
    async def events() -> AsyncIterator[str]:
        yield _sse("suggestions", {"options": prepared.suggestions or []})
        response_card = getattr(prepared, "tool_card", None)
        if response_card:
            yield _sse("tool_card", response_card)
        chunks: list[str] = []
        tool_card_sent = bool(getattr(prepared, "tool_card", None))
        try:
            async for chunk in responder.stream(
                user_message=prepared.user_message,
                **service.responder_inputs(prepared),
            ):
                chunks.append(chunk)
                yield _sse("message_delta", {"text": chunk})
        except ConversationResponseError:
            fallback = service.fallback_reply(
                prepared.user_message,
                prepared.destination_context,
            )
            chunks.append(fallback)
            yield _sse("message_delta", {"text": fallback})
        current_card = getattr(
            getattr(service, "travel_tools", None),
            "last_card",
            None,
        )
        if current_card:
            response_card = current_card
        if current_card and not tool_card_sent:
            yield _sse("tool_card", current_card)
        reply = "".join(chunks).strip()
        service.finish_message(prepared, reply, card=response_card)
        yield _sse("message_done", {"mode": "general"})

    return StreamingResponse(events(), media_type="text/event-stream")


__all__ = ["router"]
