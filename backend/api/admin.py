from __future__ import annotations

import re
import asyncio
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncio
from backend.application.admission import PlanningCapacityError
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.api.dependencies import get_planning_application_service
from backend.api.conversations import _dispatch_job, _sse, _stream_headers
from backend.application import PlanningApplicationService, PlanningConflictError
from backend.auth import CurrentUser, UserRepository, require_admin
from backend.config import Settings, get_settings
from backend.conversation import ConversationRepository, GeneralConversationRepository
from backend.database import get_db
from backend.database.tables import AttractionRow, CityRow
from backend.knowledge import KnowledgeRepository
from backend.knowledge.guide_editing import (
    GuideEditConflict, check_guide_revision, guide_revision, guide_write_lock,
    rollback_guide, write_guide_bytes,
)
from backend.models.admin import (
    AdminConversationSummary,
    AttractionKnowledgeUpdate,
    GuideDocument,
    GuideDocumentCreate,
    GuideDocumentSummary,
    GuideDocumentUpdate,
)
from backend.models.auth import UserResponse
from backend.rag.runtime import build_guide_retrieval_service, build_vector_service
from backend.rag.guide_files import guide_path, guide_paths


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _guide_path(settings: Settings, filename: str) -> Path:
    try:
        return guide_path(settings.guide_knowledge_path, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _guide_title(content: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback


async def _sync_guide_index(settings: Settings) -> int:
    return await build_guide_retrieval_service(settings).index_all()


@contextmanager
def _guide_write(settings: Settings):
    try:
        with guide_write_lock(settings.guide_knowledge_path):
            yield
    except GuideEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _sync_guide_edit(settings: Settings, path: Path, *, written: bytes | None, previous: bytes | None):
    try:
        await _sync_guide_index(settings)
    except (Exception, asyncio.CancelledError) as exc:
        rollback_guide(path, written=written, previous=previous)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(status_code=503, detail=f"知识文档已回滚，向量索引同步失败：{type(exc).__name__}") from exc


@router.get("/users", response_model=list[UserResponse])
def list_users(
    limit: int = Query(default=100, ge=1, le=500),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[UserResponse]:
    return [
        UserResponse.model_validate(row)
        for row in UserRepository(session).list_recent(limit=limit, role="user")
    ]


@router.patch("/users/{user_id}/status", response_model=UserResponse)
def update_user_status(
    user_id: str,
    active: bool = Query(...),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> UserResponse:
    row = UserRepository(session).get(user_id)
    if row is None or row.role != "user":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    row.is_active = active
    session.commit()
    session.refresh(row)
    return UserResponse.model_validate(row)


@router.get("/users/{user_id}/conversations", response_model=list[AdminConversationSummary])
def list_user_conversations(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[AdminConversationSummary]:
    user = UserRepository(session).get(user_id)
    if user is None or user.role != "user":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    planning = ConversationRepository(session, owner_id=user_id).list_recent(limit=limit)
    general = GeneralConversationRepository(session, owner_id=user_id).list_recent(limit=limit)
    result = [
        AdminConversationSummary(
            id=row.id,
            title=row.title,
            mode="planning",
            status=row.status,
            user_id=user.id,
            user_username=user.email,
            updated_at=row.updated_at,
        )
        for row in planning
    ]
    result.extend(
        AdminConversationSummary(
            id=row.id,
            title=row.title,
            mode="general",
            status=row.status,
            user_id=user.id,
            user_username=user.email,
            updated_at=row.updated_at,
        )
        for row in general
    )
    return sorted(result, key=lambda item: item.updated_at or datetime.min, reverse=True)[:limit]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    mode: str = Query(default="planning", pattern="^(planning|general)$"),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
):
    if mode == "general":
        row = GeneralConversationRepository(session).get(conversation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="普通对话不存在")
        return GeneralConversationRepository.to_model(row)
    row = ConversationRepository(session).get(conversation_id, with_messages=True)
    if row is None:
        raise HTTPException(status_code=404, detail="旅行对话不存在")
    from backend.conversation.service import ConversationService
    from backend.config import get_settings
    from backend.destination import DestinationResolver
    from backend.destination.repository import CatalogRepository
    from backend.requirements.runtime import build_requirement_parser
    from backend.conversation.responder import DeepSeekConversationResponder

    settings = get_settings()
    service = ConversationService(
        repository=ConversationRepository(session),
        parser=build_requirement_parser(settings),
        resolver=DestinationResolver(CatalogRepository(session)),
        responder=DeepSeekConversationResponder(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
    return service.get(conversation_id)


@router.post("/conversations/{conversation_id}/plan")
async def plan_conversation_for_user(
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    job_id: str | None = Query(default=None, max_length=36),
    _: CurrentUser = Depends(require_admin),
    planning_service: PlanningApplicationService = Depends(get_planning_application_service),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    if ConversationRepository(session).get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="旅行对话不存在")
    try:
        job = await asyncio.to_thread(planning_service.prepare_conversation, conversation_id)
        effective_after = after if job_id in (None, job.job_id) else 0
        queued_externally = await _dispatch_job(planning_service, job.job_id)
        event_stream = planning_service.stream_job_events(
            job.job_id,
            after=effective_after,
            start_local_worker=not queued_externally,
        )
    except PlanningCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
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


@router.get("/knowledge/attractions")
def list_attraction_knowledge(
    city: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
):
    statement = select(AttractionRow, CityRow).join(CityRow, CityRow.id == AttractionRow.city_id)
    if city:
        statement = statement.where(CityRow.name == city.strip())
    if query and query.strip():
        needle = f"%{query.strip()}%"
        statement = statement.where(
            or_(AttractionRow.name.like(needle), AttractionRow.description.like(needle))
        )
    rows = session.execute(statement.order_by(AttractionRow.name).limit(limit)).all()
    return [
        {
            "poi_id": attraction.poi_id,
            "name": attraction.name,
            "city": city_row.name,
            "description": attraction.description,
            "tags": attraction.tags or [],
        }
        for attraction, city_row in rows
    ]


@router.patch("/knowledge/attractions/{poi_id}")
def update_attraction_knowledge(
    poi_id: str,
    payload: AttractionKnowledgeUpdate,
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
):
    if not payload.description.strip():
        raise HTTPException(status_code=422, detail="景点知识内容不能为空")
    try:
        result = KnowledgeRepository(session).upsert(
            poi_id=poi_id,
            description=payload.description,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/knowledge/attractions/index")
async def rebuild_attraction_index(
    city: str = Query(min_length=2),
    _: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        count = await build_vector_service(KnowledgeRepository(session), settings).index_city(city.strip())
    except Exception as exc:  # embedding providers are optional in local development
        raise HTTPException(status_code=503, detail=f"景点知识索引刷新失败：{type(exc).__name__}") from exc
    return {"city": city.strip(), "indexed_count": count}


@router.get("/knowledge/guides", response_model=list[GuideDocumentSummary])
def list_guides(
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    result: list[GuideDocumentSummary] = []
    for path in guide_paths(settings.guide_knowledge_path):
        content = path.read_text(encoding="utf-8")
        result.append(
            GuideDocumentSummary(
                filename=path.name,
                title=_guide_title(content, path.stem),
                size=path.stat().st_size,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime),
            )
        )
    return result


@router.get("/knowledge/guides/index-status")
def guide_index_status(
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    service = build_guide_retrieval_service(settings)
    documents = service.source.list_documents()
    desired_ids = {document.doc_id for document in documents}
    existing_ids = service.vector_index.existing_ids()
    indexed_ids = existing_ids & desired_ids
    return {
        "source_files": len({document.source_file for document in documents}),
        "source_documents": len(documents),
        "indexed_documents": len(indexed_ids),
        "stale_documents": len(existing_ids - desired_ids),
        "in_sync": service.vector_index.is_current(documents) and existing_ids == desired_ids,
    }


@router.get("/knowledge/guides/{filename}", response_model=GuideDocument)
def get_guide(
    filename: str,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    path = _guide_path(settings, filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="知识文档不存在")
    raw = path.read_bytes()
    content = raw.decode("utf-8")
    return GuideDocument(filename=path.name, title=_guide_title(content, path.stem),
                         content=content, revision=guide_revision(raw))


@router.post("/knowledge/guides", response_model=GuideDocument, status_code=201)
async def create_guide(
    payload: GuideDocumentCreate,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    with _guide_write(settings):
        path = _guide_path(settings, payload.filename)
        if path.name.lower() == "readme.md":
            raise HTTPException(status_code=400, detail="README.md 仅用于说明，不能作为知识文档管理")
        if path.exists():
            raise HTTPException(status_code=409, detail="知识文档已存在")
        if not payload.content.strip():
            raise HTTPException(status_code=422, detail="知识文档不能为空")
        written = payload.content.encode("utf-8")
        write_guide_bytes(path, written)
        await _sync_guide_edit(settings, path, written=written, previous=None)
        return GuideDocument(
            filename=path.name,
            title=_guide_title(payload.content, path.stem),
            content=payload.content,
            revision=guide_revision(written),
        )


@router.put("/knowledge/guides/{filename}", response_model=GuideDocument)
async def update_guide(
    filename: str,
    payload: GuideDocumentUpdate,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    with _guide_write(settings):
        path = _guide_path(settings, filename)
        if path.name.lower() == "readme.md":
            raise HTTPException(status_code=400, detail="README.md 仅用于说明，不能作为知识文档管理")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="知识文档不存在")
        if not payload.content.strip():
            raise HTTPException(status_code=422, detail="知识文档不能为空")
        previous = path.read_bytes()
        check_guide_revision(previous, payload.expected_revision)
        written = payload.content.encode("utf-8")
        write_guide_bytes(path, written)
        await _sync_guide_edit(settings, path, written=written, previous=previous)
        return GuideDocument(
            filename=path.name,
            title=_guide_title(payload.content, path.stem),
            content=payload.content,
            revision=guide_revision(written),
        )


@router.delete("/knowledge/guides/{filename}", status_code=204)
async def delete_guide(
    filename: str,
    expected_revision: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    with _guide_write(settings):
        path = _guide_path(settings, filename)
        if path.name.lower() == "readme.md":
            raise HTTPException(status_code=400, detail="README.md 仅用于说明，不能删除")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="知识文档不存在")
        previous = path.read_bytes()
        check_guide_revision(previous, expected_revision)
        path.unlink()
        await _sync_guide_edit(settings, path, written=None, previous=previous)
        return None


@router.post("/knowledge/guides/index")
async def rebuild_guide_index(
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    with _guide_write(settings):
        try:
            count = await _sync_guide_index(settings)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"旅行指南索引刷新失败：{type(exc).__name__}") from exc
        return {"indexed_count": count}


__all__ = ["router"]
