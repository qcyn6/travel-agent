import json
import logging
import asyncio
import time
from uuid import uuid4
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
from backend.conversation.fallback import summarize_tool_cards
from backend.conversation.streaming import text_and_progress
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
logger = logging.getLogger(__name__)


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
        max_output_tokens=settings.general_max_output_tokens,
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
    deadline = time.monotonic() + settings.general_turn_timeout_seconds
    request_id = uuid4().hex
    repository = _owned_repository(repository, user)
    service = _general_service(repository, session, settings)
    responder = _general_responder(settings)
    progress: asyncio.Queue[str] = asyncio.Queue()
    configure_budget = getattr(responder, "configure_budget", None)
    if callable(configure_budget):
        configure_budget(deadline=deadline, timeouts={
            "amap_route": settings.general_route_timeout_seconds,
            "qweather_forecast": settings.general_weather_timeout_seconds,
            "amap_nearby_search": settings.general_nearby_timeout_seconds,
            "travel_guide_search": settings.general_guide_timeout_seconds,
        }, on_progress=progress.put_nowait, request_id=request_id)
    configure_tools = getattr(responder, "configure_tools", None)
    if callable(configure_tools):
        configure_tools(
            travel_tools=getattr(service, "travel_tools", None),
            guide_retriever=getattr(service, "knowledge_retriever", None),
        )
    try:
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            prepared = await _prepare_general_turn(service, conversation_id, payload.message)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="本次请求准备超时，请重试。") from exc
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    refresh_memory = getattr(service, "refresh_memory", None)
    summarize_memory = getattr(responder, "summarize_memory", None)
    async def events() -> AsyncIterator[str]:
        yield _sse("suggestions", {"options": prepared.suggestions or []})
        response_card = getattr(prepared, "tool_card", None)
        if response_card:
            yield _sse("tool_card", response_card)
        chunks: list[str] = []
        failed = False
        tool_card_sent = bool(getattr(prepared, "tool_card", None))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                if callable(refresh_memory) and callable(summarize_memory):
                    try:
                        async with asyncio.timeout(settings.general_memory_timeout_seconds):
                            await refresh_memory(prepared, summarize_memory)
                    except TimeoutError:
                        logger.warning("普通对话摘要超时，使用原历史窗口 conversation_id=%s", conversation_id)
                yield _sse("message_progress", {"message": "正在生成回复"})
                stream = responder.stream(
                    user_message=prepared.user_message,
                    **service.responder_inputs(prepared),
                )
                async for event, data in text_and_progress(stream, progress):
                    if event == 'message_delta':
                        chunks.append(data['text'])
                    yield _sse(event, data)
            if not "".join(chunks).strip():
                raise ConversationResponseError("普通模式返回了空回复")
        except (ConversationResponseError, TimeoutError) as exc:
            failed = True
            logger.warning("普通对话回复失败 request_id=%s conversation_id=%s error_type=%s cause_type=%s",
                request_id, conversation_id, type(exc).__name__, type(exc.__cause__).__name__ if exc.__cause__ else "none")
            notice = "本次回复生成失败，请重试。"
            if isinstance(exc, TimeoutError):
                notice = "本次查询已达到时间上限，请重试或缩小问题范围。"
            if not "".join(chunks).strip():
                available_card = getattr(getattr(service, "travel_tools", None), "last_card", None)
                summary = summarize_tool_cards(available_card)
                if summary:
                    summary = "已查到以下结果：\n" + summary + "\n\n"
                    chunks.append(summary)
                    yield _sse("message_delta", {"text": summary})
                    notice = "自动整理回复未完成，以上为已查询到的结果；其他未完成内容请重试。"
            elif "".join(chunks).strip():
                notice = "\n\n本次回复中断，以上内容可能不完整，请重试。"
            chunks.append(notice)
            yield _sse("message_delta", {"text": notice})
            yield _sse("message_error", {"message": notice.strip(), "retryable": True})
        except asyncio.CancelledError:
            # Do not turn disconnect/cancellation into a retry or show cards.
            stopped = "".join(chunks).strip()
            try:
                service.finish_message(prepared, (stopped + "\n\n已停止生成。").strip(), card=None)
            except Exception as exc:
                logger.warning('普通对话停止记录保存失败 request_id=%s error_type=%s', request_id, type(exc).__name__)
            raise
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
        try:
            service.finish_message(prepared, reply, card=response_card)
        except Exception as exc:
            logger.warning('普通对话回复保存失败 request_id=%s error_type=%s', request_id, type(exc).__name__)
            yield _sse('message_error', {'message': '回复未能保存，请刷新后检查历史记录。', 'retryable': False})
            return
        yield _sse("message_done", {"mode": "general", "status": "failed" if failed else "completed"})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        'X-Request-Id': request_id, 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
    })


__all__ = ["router"]
