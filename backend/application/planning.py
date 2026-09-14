import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from backend.config import Settings
from backend.conversation import ConversationNotFoundError, ConversationRepository
from backend.database import open_session
from backend.destination import DestinationPlanningError
from backend.knowledge import KnowledgeBootstrapError
from backend.models.conversation import RequirementDraft
from backend.models.travel_plan import DestinationChoiceResponse, TravelPlanResponse
from backend.planner import DestinationChoiceRequired, build_travel_workflow
from backend.planning_jobs import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    PlanningJobRepository,
)
from backend.travel_plans import TravelPlanRepository


PLANNING_POLL_SECONDS = 0.5


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

    def __init__(self, settings: Settings, *, task_registry: PlanningTaskRegistry | None = None):
        self.settings = settings
        self.task_registry = task_registry or _planning_tasks

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

            requirement = RequirementDraft.model_validate(conversation.requirement_draft or {})
            job = job_repository.create(conversation_id, requirement)
            job_repository.append_event(job, "job_queued", {"status": "queued"})
            conversation_repository.set_status(conversation, "planning", commit=False)
            session.commit()
            return self._job_handle(job)

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
    ) -> AsyncIterator[tuple[str, dict]]:
        """Compatibility wrapper that streams the latest job for a conversation."""

        with open_session() as session:
            job = PlanningJobRepository(session).latest_for_conversation(conversation_id)
            if job is None:
                raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
            job_id = job.id
        return self.stream_job_events(job_id, after=after)

    def stream_job_events(self, job_id: str, *, after: int = 0) -> AsyncIterator[tuple[str, dict]]:
        """Replay committed events and poll until the job reaches a terminal state."""

        self._ensure_worker(job_id)

        async def events() -> AsyncIterator[tuple[str, dict]]:
            cursor = max(0, int(after))
            while True:
                status, new_events = self._read_events(job_id, after=cursor)
                if status is None:
                    raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
                for sequence, event_type, payload in new_events:
                    cursor = sequence
                    yield event_type, payload
                if status in TERMINAL_JOB_STATUSES:
                    break
                await asyncio.sleep(PLANNING_POLL_SECONDS)

        return events()

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
            except Exception:
                session.rollback()
                conversation = conversation_repository.get(conversation_id, for_update=True)
                if conversation is not None:
                    recovery_status = "confirmed" if conversation.requirement_confirmed else "draft"
                    conversation_repository.set_status(conversation, recovery_status, commit=False)
                job = job_repository.get(job.id, for_update=True)
                if job is not None:
                    job_repository.mark_failed(job, "旅游方案生成失败，请稍后重试")
                    job_repository.append_event(
                        job,
                        "planning_error",
                        {"message": "旅游方案生成失败，请稍后重试"},
                    )
                session.commit()
                raise

    def _ensure_worker(self, job_id: str) -> None:
        if self.task_registry.is_running(job_id):
            return
        with open_session() as session:
            job = PlanningJobRepository(session).get(job_id)
            if job is None:
                raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
            if job.status not in ACTIVE_JOB_STATUSES:
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
        if not self._claim_job(job_id):
            return

        active_step: tuple[str, str] | None = None

        async def progress(step: str, label: str, status: str) -> None:
            nonlocal active_step
            if status == "running":
                active_step = (step, label)
            elif active_step is not None and active_step[0] == step:
                active_step = None
            self._record_progress(job_id, step=step, label=label, status=status)

        with open_session() as session:
            conversation_repository = ConversationRepository(session)
            job_repository = PlanningJobRepository(session)
            plan_repository = TravelPlanRepository(session)
            job = job_repository.get(job_id)
            if job is None:
                return
            conversation_id = job.conversation_id

            try:
                requirement = RequirementDraft.model_validate(job.requirement_json or {})
                workflow = build_travel_workflow(session, self.settings)
                result = await workflow.run_requirement(requirement, progress=progress)

                # Keep lock order consistent with prepare_conversation: owning
                # conversation first, then its planning job.
                conversation = conversation_repository.get(conversation_id, for_update=True)
                if conversation is None:
                    raise ConversationNotFoundError("当前会话不存在或已失效")
                job = job_repository.get(job_id, for_update=True)
                if job is None:
                    raise ConversationNotFoundError("当前旅行计划任务不存在或已失效")
                plan_repository.save(conversation_id, result, commit=False)
                conversation_repository.set_status(conversation, "completed", commit=False)
                job_repository.set_requirement(
                    job,
                    RequirementDraft.model_validate(result.requirement.model_dump()),
                )
                job_repository.mark_completed(job)
                job_repository.append_event(job, "plan_completed", result.model_dump(mode="json"))
                session.commit()
            except DestinationChoiceRequired as exc:
                self._finish_choice(
                    session,
                    conversation_repository,
                    job_repository,
                    job_id,
                    exc,
                )
            except (ValueError, DestinationPlanningError) as exc:
                self._finish_failure(
                    session,
                    conversation_repository,
                    job_repository,
                    job_id,
                    str(exc),
                    active_step,
                )
            except KnowledgeBootstrapError as exc:
                self._finish_failure(
                    session,
                    conversation_repository,
                    job_repository,
                    job_id,
                    str(exc),
                    active_step,
                )
            except Exception:
                self._finish_failure(
                    session,
                    conversation_repository,
                    job_repository,
                    job_id,
                    "旅游方案生成失败，请稍后重试",
                    active_step,
                )

    def _claim_job(self, job_id: str) -> bool:
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id, for_update=True)
            if job is None or job.status in TERMINAL_JOB_STATUSES:
                return False
            # The MVP runs one local worker process. PlanningTaskRegistry
            # serializes workers inside that process; a new process is allowed
            # to reclaim a running row after restart so a fresh heartbeat from
            # the dead process cannot leave the stream waiting forever.
            repository.mark_running(job)
            repository.append_event(job, "job_started", {"status": "running"})
            session.commit()
            return True

    def _record_progress(self, job_id: str, *, step: str, label: str, status: str) -> None:
        with open_session() as session:
            repository = PlanningJobRepository(session)
            job = repository.get(job_id, for_update=True)
            if job is None or job.status not in ACTIVE_JOB_STATUSES:
                return
            repository.record_progress(job, step=step, label=label, status=status)
            session.commit()

    @staticmethod
    def _finish_choice(
        session,
        conversation_repository: ConversationRepository,
        job_repository: PlanningJobRepository,
        job_id: str,
        choice: DestinationChoiceRequired,
    ) -> None:
        session.rollback()
        job = job_repository.get(job_id)
        if job is None:
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
        if job is None:
            return
        job_repository.set_requirement(job, requirement)
        job_repository.mark_waiting_for_choice(job)
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
    ) -> None:
        session.rollback()
        job = job_repository.get(job_id)
        if job is None:
            return
        conversation = conversation_repository.get(job.conversation_id, for_update=True)
        if conversation is not None:
            recovery_status = "confirmed" if conversation.requirement_confirmed else "draft"
            conversation_repository.set_status(conversation, recovery_status, commit=False)
        job = job_repository.get(job_id, for_update=True)
        if job is None:
            return
        if active_step is not None:
            step, label = active_step
            job_repository.record_progress(job, step=step, label=label, status="failed")
        job_repository.mark_failed(job, message)
        job_repository.append_event(job, "planning_error", {"message": message})
        session.commit()

    @staticmethod
    def _job_handle(job) -> PlanningJobHandle:
        return PlanningJobHandle(
            job_id=job.id,
            conversation_id=job.conversation_id,
            requirement=RequirementDraft.model_validate(job.requirement_json or {}),
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
