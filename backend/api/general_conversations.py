import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.config import Settings, get_settings
from backend.conversation import ConversationNotFoundError
from backend.conversation.general_repository import (
    GeneralConversationRepository,
    get_general_conversation_repository,
)
from backend.conversation.general_service import GeneralConversationService
from backend.conversation.responder import ConversationResponseError, DeepSeekConversationResponder
from backend.database import get_db
from backend.destination import DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.models.conversation import (
    ConversationSummary,
    GeneralChatMessageRequest,
    GeneralConversation,
)
from backend.rag.runtime import build_knowledge_retrieval_service


router = APIRouter(prefix="/api/v1/general-conversations", tags=["general-conversations"])


def _general_service(repository, session, settings: Settings) -> GeneralConversationService:
    return GeneralConversationService(
        repository=repository,
        resolver=DestinationResolver(CatalogRepository(session)),
        knowledge_retriever=build_knowledge_retrieval_service(settings),
    )


def _general_responder(settings: Settings) -> DeepSeekConversationResponder:
    return DeepSeekConversationResponder(
        settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("", response_model=GeneralConversation, status_code=201)
def create_general_conversation(
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
) -> GeneralConversation:
    return repository.to_model(repository.create())


@router.get("", response_model=list[ConversationSummary])
def list_general_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
) -> list[ConversationSummary]:
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
) -> GeneralConversation:
    row = repository.get(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="当前普通对话不存在或已失效")
    return repository.to_model(row)


@router.delete("/{conversation_id}", status_code=204)
def delete_general_conversation(
    conversation_id: str,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
) -> None:
    if not repository.delete(conversation_id):
        raise HTTPException(status_code=404, detail="当前普通对话不存在或已失效")


@router.post("/{conversation_id}/messages")
async def send_general_message(
    conversation_id: str,
    payload: GeneralChatMessageRequest,
    repository: GeneralConversationRepository = Depends(get_general_conversation_repository),
    settings: Settings = Depends(get_settings),
    session=Depends(get_db),
) -> StreamingResponse:
    service = _general_service(repository, session, settings)
    responder = _general_responder(settings)
    try:
        prepared = await service.prepare_message(conversation_id, payload.message)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        yield _sse("suggestions", {"options": ["你能做什么？", "介绍一个目的地"]})
        chunks: list[str] = []
        if prepared.boundary_reply:
            reply = prepared.boundary_reply
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        elif prepared.quick_reply:
            reply = prepared.quick_reply
            chunks.append(reply)
            yield _sse("message_delta", {"text": reply})
        else:
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
        reply = "".join(chunks).strip()
        service.finish_message(prepared, reply)
        yield _sse("message_done", {"mode": "general"})

    return StreamingResponse(events(), media_type="text/event-stream")


__all__ = ["router"]
