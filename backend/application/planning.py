import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
import logging
import time
from uuid import uuid4

from backend.config import Settings
from backend.conversation import ConversationNotFoundError, ConversationRepository
from backend.database import open_session
from backend.destination import DestinationPlanningError
from backend.knowledge import KnowledgeBootstrapError
from backend.models.conversation import RequirementDraft, load_requirement_draft
from backend.models.travel_plan import DestinationChoiceResponse, TravelPlanResponse
from backend.observability import AlertSink, build_alert_sink, metrics
from backend.planner import DestinationChoiceRequired, build_travel_workflow
from backend.planning_jobs import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    PlanningJobRepository,
)
from backend.planning_jobs.queue import PlanningQueue, build_planning_queue
from backend.travel_plans import TravelPlanRepository


PLANNING_POLL_SECONDS = 0.5
PLANNING_KEEPALIVE_SECONDS = 15.0
logger = logging.getLogger(__name__)


class PlanningConflictError(RuntimeError):
    """Raised when a conversation cannot start another plan generation yet."""


@dataclass(frozen=True)
class PlanningJobHandle:
    job_id: str
    conversation_id: str
    requirement: RequirementDraft


@dataclass(frozen=True)
class StandalonePlanningResult:
    conversation_id: str
    response: TravelPlanResponse | DestinationChoiceResponse


class PlanningTaskRegistry:
    """Tracks local workers while the database remains the source of job state."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_running(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None:
            return False
        if task.done():
            self._tasks.pop(job_id, None)
            return False
        return True

    def start(self, job_id: str, work: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        if self.is_running(job_id):
            raise PlanningConflictError("当前旅行计划正在生成中，请稍候")
        task = asyncio.create_task(work())
        self._tasks[job_id] = task

        def cleanup(done: asyncio.Task[None]) -> None:
            if self._tasks.get(job_id) is done:
                self._tasks.pop(job_id, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(cleanup)
        return task


_planning_tasks = PlanningTaskRegistry()


class PlanningApplicationService:
    """Application boundary for durable, conversation-backed planning jobs."""

    def __init__(
        self,
        settings: Settings,
        *,
        task_registry: PlanningTaskRegistry | None = None,
        queue: PlanningQueue | None = None,
        alert_sink: AlertSink | None = None,
    ):
        self.settings = settings
        self.task_registry = task_registry or _planning_tasks
        self.queue = queue or build_planning_queue(settings)
        self.alert_sink = alert_sink or build_alert_sink(settings)

    def prepare_conversation(self, conversation_id: str) -> PlanningJobHandle:
        """Create or reuse a durable job and mark the conversation as planning."""

        with open_session() as session:
            conversation_repository = ConversationRepository(session)
            job_repository = PlanningJobRepository(session)
            conversation = conversation_repository.get(conversation_id, for_update=True)
            if conversation is None:
                raise ConversationNotFoundError("当前会话不存在或已失效")

            active_job = job_repository.active_for_conversation(
                conversation_id,
                for_update=True,
            )
            if active_job is not None:
                if conversation.status != "planning":
                    conversation_repository.set_status(conversation, "planning", commit=False)
                    session.commit()
                return self._job_handle(active_job)

            if conversation.status == "planning":
                # The conversation marker may survive a process crash after the
                # old job stopped. Reset it before allowing a fresh attempt.
                recovery_status = "confirmed" if conversation.requirement_confirmed else "draft"
                conversation_repository.set_status(conversation, recovery_status, commit=False)

            if not conversation.requirement_confirmed:
                session.commit()
                raise PlanningConflictError("请先确认旅行需求")

            requirement = load_requirement_draft(conversation.requirement_draft or {})
            if not requirement.is_ready():
                session.commit()
                raise PlanningConflictError("当前旅行需求尚未完整，请先在对话中补充所有出行条件")
            job = job_repository.create(conversation_id, requirement)
            job_repository.append_event(job, "job_queued", {"status": "queued"})
            conversation_repository.set_status(conversation, "planning", commit=False)
            session.commit()
            metrics.inc(
                "travel_agent_planning_jobs_total",
                {"status": "queued"},
            )
            return self._job_handle(job)

    async def dispatch_job(self, job_id: str) -> bool:
        """Publish a job to the external queue when it is configured.

        A queue outage falls back to the existing local worker so development
        and single-process deployments remain usable.
        """

        if not getattr(self.queue, "enabled", False):
            return False
        try:
            accepted = await self.queue.enqueue(job_id)
        except Exception as exc:
            logger.exception("planning queue dispatch failed: job_id=%s", job_id)
            accepted = False
            error_type = type(exc).__name__
        else:
            error_type = "QueueUnavailable"
        if accepted:
            return True

        metrics.inc(
            "travel_agent_planning_queue_enqueues_total",
            {"outcome": "local_fallback"},
        )
        await self._notify_alert(
            event="planning_queue_unavailable",
            severity="warning",
            message="规划任务队列不可用，已回退到进程内 worker",
            metadata={"job_id": job_id, "error_type": error_type},
        )
        self._ensure_worker(job_id)
        return False

    def latest_job_id(self, conversation_id: str) -> str | None:
        with open_session() as session:
            job = PlanningJobRepository(session).latest_for_conversation(conversation_id)
            return job.id if job is not None else None

    def stream_conversation_plan(
        self,
        conversation_id: str,
        requirement: RequirementDraft | None = None,
        *,
        after: int = 0,
    ) -> AsyncIterator[tuple[int | None, str, dict]]:
        """Compatibility wrapper that streams the latest job for a conversation."""

        with open_session() as session:
            job = PlanningJobRepository(session).latest_for_conversation(conversation_id)
            if job is None:
                raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
            job_id = job.id
        return self.stream_job_events(job_id, after=after)

    def stream_job_events(
        self,
        job_id: str,
        *,
        after: int = 0,
        start_local_worker: bool = True,
    ) -> AsyncIterator[tuple[int | None, str, dict]]:
        """Replay committed events and poll until the job reaches a terminal state."""

        if start_local_worker:
            self._ensure_worker(job_id)

        async def events() -> AsyncIterator[tuple[int | None, str, dict]]:
            started = time.perf_counter()
            metrics.change_gauge("travel_agent_planning_sse_connections", 1)
            try:
                cursor = max(0, int(after))
                last_keepalive = asyncio.get_running_loop().time()
                while True:
                    status, new_events = self._read_events(job_id, after=cursor)
                    if status is None:
                        raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
                    for sequence, event_type, payload in new_events:
                        cursor = sequence
                        last_keepalive = asyncio.get_running_loop().time()
                        yield sequence, event_type, payload
                    if status in TERMINAL_JOB_STATUSES:
                        break
                    # A process that originally owned the job may have died after
                    # the first response was opened. Re-check the lease on every
                    # poll so a different process can reclaim it after timeout.
                    if start_local_worker:
                        self._ensure_worker(job_id)
                    now = asyncio.get_running_loop().time()
                    if now - last_keepalive >= PLANNING_KEEPALIVE_SECONDS:
                        last_keepalive = now
                        yield None, "heartbeat", {"status": status}
                    sleep_seconds = min(
                        PLANNING_POLL_SECONDS,
                        max(0.05, PLANNING_KEEPALIVE_SECONDS - (now - last_keepalive)),
                    )
                    await asyncio.sleep(sleep_seconds)
            finally:
                metrics.change_gauge("travel_agent_planning_sse_connections", -1)
                metrics.observe(
                    "travel_agent_planning_sse_duration_seconds",
                    time.perf_counter() - started,
                )

        return events()

    async def run_queued_job(self, job_id: str) -> None:
        """Run one durable job claimed from the external queue."""

        await self._run_job(job_id)

    async def run_standalone(
        self,
        text: str,
        *,
        travel_scope=None,
    ) -> StandalonePlanningResult:
        """Run the legacy one-shot endpoint while keeping the same history/job model."""

        with open_session() as session:
            conversation_repository = ConversationRepository(session)
            job_repository = PlanningJobRepository(session)
            conversation = conversation_repository.create()
            conversation_id = conversation.id
            conversation_repository.add_message(conversation, role="user", content=text.strip())
            conversation_repository.set_status(conversation, "planning", commit=False)
            job = job_repository.create(conversation_id, RequirementDraft())
            job_repository.mark_running(job)
            job_repository.append_event(job, "job_started", {"status": "running"})
            # Publish the durable running marker before the external workflow
            # starts. A disconnected client or a second request can now see
            # that this job is active instead of observing an empty history.
            session.commit()

            try:
                workflow = build_travel_workflow(session, self.settings)
                result = await workflow.run(text, travel_scope=travel_scope)
                requirement = RequirementDraft.model_validate(result.requirement.model_dump())
                conversation_repository.save_requirement(
                    conversation,
                    requirement,
                    confirmed=True,
                    commit=False,
                )
                job_repository.set_requirement(job, requirement)
                plan_repository = TravelPlanRepository(session)
                plan_repository.save(conversation_id, result, commit=False)
                conversation_repository.set_status(conversation, "completed", commit=False)
                job_repository.mark_completed(job)
                job_repository.append_event(job, "plan_completed", result.model_dump(mode="json"))
                session.commit()
                return StandalonePlanningResult(conversation_id, result)
            except DestinationChoiceRequired as exc:
                session.rollback()
                conversation = conversation_repository.get(conversation_id, for_update=True)
                if conversation is None:
                    raise ConversationNotFoundError("当前会话不存在或已失效")
                requirement = RequirementDraft.model_validate(exc.requirement.model_dump())
                conversation_repository.save_requirement(
                    conversation,
                    requirement,
                    confirmed=False,
                    commit=False,
                )
                job = job_repository.get(job.id, for_update=True)
                if job is not None:
                    job_repository.set_requirement(job, requirement)
                    job_repository.mark_waiting_for_choice(job)
                    job_repository.append_event(
                        job,
                        "destination_choice_required",
                        {
                            "requirement": exc.requirement.model_dump(mode="json"),
                            "options": exc.options.model_dump(mode="json"),
                        },
                    )
                session.commit()
                return StandalonePlanningResult(
                    conversation_id,
                    DestinationChoiceResponse(
                        requirement=exc.requirement,
                        destination_options=exc.options,
                    ),
                )
            except Exception as exc:
                session.rollback()
                conversation = conversation_repository.get(conversation_id, for_update=True)
                if conversation is not None:
                    recovery_status = "confirmed" if conversation.requirement_confirmed else "draft"
                    conversation_repository.set_status(conversation, recovery_status, commit=False)
                job = job_repository.get(job.id, for_update=True)
                if job is not None:
                    message = self._failure_message(exc)
                    job_repository.mark_failed(job, message)
                    job_repository.append_event(
                        job,
                        "planning_error",
                        {"message": message},
                    )
                session.commit()
                # Preserve the durable identity for callers that need to
                # clean up a disposable request or inspect its failed job.
                setattr(exc, "conversation_id", conversation_id)
                raise

    def _ensure_worker(self, job_id: str) -> None:
        if self.task_registry.is_running(job_id):
            return
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
            if job.status not in ACTIVE_JOB_STATUSES:
                return
            if job.status == "running" and not repository.is_stale(
                job,
                stale_after_seconds=self._worker_stale_seconds(),
            ):
                # Another process currently owns a fresh lease. The polling
                # loop will try again after the lease expires if needed.
                return
        try:
            self.task_registry.start(job_id, lambda: self._run_job(job_id))
        except PlanningConflictError:
            # Two browser requests can observe the same active job before
            # either one yields. The first request owns the local worker; the
            # second should simply attach to its persisted event stream.
            if not self.task_registry.is_running(job_id):
                raise

    async def _run_job(self, job_id: str) -> None:
        started = time.perf_counter()
        worker_id = uuid4().hex
        if not self._claim_job(job_id, worker_id=worker_id):
            metrics.inc(
                "travel_agent_planning_jobs_total",
                {"status": "not_claimed"},
            )
            return

        metrics.change_gauge("travel_agent_planning_active_jobs", 1)
        metrics.inc(
            "travel_agent_planning_jobs_total",
            {"status": "running"},
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id, worker_id))
        active_step: tuple[str, str] | None = None
        outcome = "aborted"

        async def progress(step: str, label: str, status: str) -> None:
            nonlocal active_step
            if status == "running":
                active_step = (step, label)
            elif active_step is not None and active_step[0] == step:
                active_step = None
            self._record_progress(
                job_id,
                worker_id=worker_id,
                step=step,
                label=label,
                status=status,
            )

        try:
            with open_session() as session:
                conversation_repository = ConversationRepository(session)
                job_repository = PlanningJobRepository(session)
                plan_repository = TravelPlanRepository(session)
                job = job_repository.get(job_id)
                if job is None:
                    outcome = "job_missing"
                    return
                conversation_id = job.conversation_id

                try:
                    requirement = load_requirement_draft(job.requirement_json or {})
                    workflow = build_travel_workflow(session, self.settings)
                    result = await workflow.run_requirement(requirement, progress=progress)

                    # Keep lock order consistent with prepare_conversation: owning
                    # conversation first, then its planning job.
                    conversation = conversation_repository.get(conversation_id, for_update=True)
                    if conversation is None:
                        raise ConversationNotFoundError("当前会话不存在或已失效")
                    job = job_repository.get(job_id, for_update=True)
                    if job is None or not job_repository.owns(job, worker_id):
                        # A stale worker must never publish a result after a
                        # different process has reclaimed the lease.
                        return
                    plan_repository.save(conversation_id, result, commit=False)
                    conversation_repository.set_status(conversation, "completed", commit=False)
                    job_repository.set_requirement(
                        job,
                        RequirementDraft.model_validate(result.requirement.model_dump()),
                    )
                    job_repository.mark_completed(job, worker_id=worker_id)
                    job_repository.append_event(job, "plan_completed", result.model_dump(mode="json"))
                    session.commit()
                    outcome = "completed"
                    metrics.inc(
                        "travel_agent_planning_jobs_total",
                        {"status": "completed"},
                    )
                except DestinationChoiceRequired as exc:
                    self._finish_choice(
                        session,
                        conversation_repository,
                        job_repository,
                        job_id,
                        exc,
                        worker_id,
                    )
                    outcome = "waiting_for_choice"
                    metrics.inc(
                        "travel_agent_planning_jobs_total",
                        {"status": "waiting_for_choice"},
                    )
                except (ValueError, DestinationPlanningError) as exc:
                    failed = self._finish_failure(
                        session,
                        conversation_repository,
                        job_repository,
                        job_id,
                        str(exc),
                        active_step,
                        worker_id,
                    )
                    outcome = "failed" if failed else "lease_lost"
                    if failed:
                        metrics.inc(
                            "travel_agent_planning_jobs_total",
                            {"status": "failed"},
                        )
                        await self._notify_alert(
                            event="planning_job_failed",
                            severity="error",
                            message="规划任务执行失败",
                            metadata={
                                "job_id": job_id,
                                "error_type": type(exc).__name__,
                            },
                        )
                except KnowledgeBootstrapError as exc:
                    failed = self._finish_failure(
                        session,
                        conversation_repository,
                        job_repository,
                        job_id,
                        str(exc),
                        active_step,
                        worker_id,
                    )
                    outcome = "failed" if failed else "lease_lost"
                    if failed:
                        metrics.inc(
                            "travel_agent_planning_jobs_total",
                            {"status": "failed"},
                        )
                        await self._notify_alert(
                            event="planning_job_failed",
                            severity="error",
                            message="规划任务执行失败",
                            metadata={
                                "job_id": job_id,
                                "error_type": type(exc).__name__,
                            },
                        )
                except Exception as exc:
                    logger.exception("planning job failed during workflow execution: job_id=%s", job_id)
                    message = self._failure_message(exc)
                    failed = self._finish_failure(
                        session,
                        conversation_repository,
                        job_repository,
                        job_id,
                        message,
                        active_step,
                        worker_id,
                    )
                    outcome = "failed" if failed else "lease_lost"
                    if failed:
                        metrics.inc(
                            "travel_agent_planning_jobs_total",
                            {"status": "failed"},
                        )
                        await self._notify_alert(
                            event="planning_job_failed",
                            severity="error",
                            message="规划任务执行失败",
                            metadata={
                                "job_id": job_id,
                                "error_type": type(exc).__name__,
                            },
                        )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            metrics.change_gauge("travel_agent_planning_active_jobs", -1)
            metrics.observe(
                "travel_agent_planning_job_duration_seconds",
                time.perf_counter() - started,
                {"status": outcome},
            )

    def _claim_job(self, job_id: str, *, worker_id: str) -> bool:
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id, for_update=True)
            if job is None:
                return False
            if not repository.claim(
                job,
                worker_id=worker_id,
                stale_after_seconds=self._worker_stale_seconds(),
            ):
                return False
            repository.append_event(job, "job_started", {"status": "running"})
            session.commit()
            if job.retry_count > 1:
                metrics.inc("travel_agent_planning_lease_reclaims_total")
            return True

    async def _heartbeat_loop(self, job_id: str, worker_id: str) -> None:
        interval = self._heartbeat_seconds()
        while True:
            await asyncio.sleep(interval)
            try:
                with open_session() as session:
                    repository = PlanningJobRepository(session)
                    job = repository.get(job_id, for_update=True)
                    if job is None or not repository.touch_heartbeat(job, worker_id=worker_id):
                        return
                    session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A transient heartbeat failure must not cancel the planning
                # workflow. The lease will naturally expire if the database
                # remains unavailable.
                continue

    def _record_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        step: str,
        label: str,
        status: str,
    ) -> None:
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id, for_update=True)
            if (
                job is None
                or job.status not in ACTIVE_JOB_STATUSES
                or not repository.owns(job, worker_id)
            ):
                return
            repository.record_progress(
                job,
                step=step,
                label=label,
                status=status,
                worker_id=worker_id,
            )
            session.commit()

    def _worker_stale_seconds(self) -> float:
        return max(1.0, float(getattr(self.settings, "planning_worker_stale_seconds", 90.0)))

    @staticmethod
    def _failure_message(exc: Exception) -> str:
        """Return a useful, bounded reason for the user-facing failure panel.

        Known workflow errors already carry a Chinese product message. For
        uncategorized errors, keeping the exception text is more useful than
        replacing every failure with the same generic sentence. The database
        repository still applies its 2000-character limit when persisting it.
        """

        message = str(exc).strip()
        if message:
            return message
        return f"{type(exc).__name__}：服务未返回具体错误信息"

    def _heartbeat_seconds(self) -> float:
        return max(0.5, float(getattr(self.settings, "planning_heartbeat_seconds", 10.0)))

    async def _notify_alert(
        self,
        *,
        event: str,
        severity: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        try:
            await self.alert_sink.notify(
                event=event,
                severity=severity,
                message=message,
                metadata=metadata,
            )
        except Exception:
            logger.exception("operator alert delivery failed: event=%s", event)

    @staticmethod
    def _finish_choice(
        session,
        conversation_repository: ConversationRepository,
        job_repository: PlanningJobRepository,
        job_id: str,
        choice: DestinationChoiceRequired,
        worker_id: str,
    ) -> None:
        session.rollback()
        job = job_repository.get(job_id)
        if job is None or not job_repository.owns(job, worker_id):
            return
        conversation = conversation_repository.get(job.conversation_id, for_update=True)
        if conversation is None:
            return
        requirement = RequirementDraft.model_validate(choice.requirement.model_dump())
        conversation_repository.save_requirement(
            conversation,
            requirement,
            confirmed=True,
            commit=False,
        )
        job = job_repository.get(job_id, for_update=True)
        if job is None or not job_repository.owns(job, worker_id):
            return
        job_repository.set_requirement(job, requirement)
        if not job_repository.mark_waiting_for_choice(job, worker_id=worker_id):
            return
        job_repository.append_event(
            job,
            "destination_choice_required",
            {
                "requirement": choice.requirement.model_dump(mode="json"),
                "options": choice.options.model_dump(mode="json"),
            },
        )
        session.commit()

    @staticmethod
    def _finish_failure(
        session,
        conversation_repository: ConversationRepository,
        job_repository: PlanningJobRepository,
        job_id: str,
        message: str,
        active_step: tuple[str, str] | None,
        worker_id: str,
    ) -> bool:
        session.rollback()
        job = job_repository.get(job_id)
        if job is None or not job_repository.owns(job, worker_id):
            return False
        conversation = conversation_repository.get(job.conversation_id, for_update=True)
        if conversation is not None:
            recovery_status = "confirmed" if conversation.requirement_confirmed else "draft"
            conversation_repository.set_status(conversation, recovery_status, commit=False)
        job = job_repository.get(job_id, for_update=True)
        if job is None or not job_repository.owns(job, worker_id):
            return False
        if active_step is not None:
            step, label = active_step
            job_repository.record_progress(
                job,
                step=step,
                label=label,
                status="failed",
                worker_id=worker_id,
            )
        if not job_repository.mark_failed(job, message, worker_id=worker_id):
            return False
        job_repository.append_event(job, "planning_error", {"message": message})
        session.commit()
        return True

    @staticmethod
    def _job_handle(job) -> PlanningJobHandle:
        return PlanningJobHandle(
            job_id=job.id,
            conversation_id=job.conversation_id,
            requirement=load_requirement_draft(job.requirement_json or {}),
        )

    @staticmethod
    def _read_events(
        job_id: str,
        *,
        after: int,
    ) -> tuple[str | None, list[tuple[int, str, dict]]]:
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id)
            if job is None:
                return None, []
            events = repository.list_events(job_id, after=after)
            return job.status, [
                (event.sequence_number, event.event_type, event.payload or {})
                for event in events
            ]
