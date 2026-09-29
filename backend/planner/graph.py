"""LangGraph orchestration for confirmed travel-plan generation."""

from __future__ import annotations

import asyncio
from inspect import isawaitable
from pathlib import Path
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.base import maybe_add_typed_methods
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command, interrupt

from backend.planner.city import CityPlanningError
from backend.planner.state import PlanningContext, PlanningState
from backend.planner.subgraphs import (
    build_city_planning_subgraph,
    build_enrichment_subgraph,
    build_quality_subgraph,
    build_requirement_subgraph,
)


# Backwards-compatible public name used by existing callers and tests.
TravelPlanningGraphState = PlanningState


class TravelPlanningGraph:
    """Orchestrate the existing planning services as explicit graph nodes.

    Domain algorithms stay behind the workflow's planning-algorithm port.
    The top-level graph composes requirement, city-planning, enrichment, and
    quality-control subgraphs; the city subgraph owns the bounded recovery
    branch when a city cannot produce a route-aware plan.
    """

    def __init__(self, workflow, *, checkpoint_path: str | None = None):
        self.workflow = workflow
        self.checkpoint_path = checkpoint_path
        # Keep an unbound graph for inspection, Mermaid rendering, and the
        # existing callers that only need the topology. Runtime executions
        # use a durable SQLite saver when one is configured.
        self.graph = self._build_graph()

    def _build_graph(self, *, checkpointer=None):
        requirement_subgraph = build_requirement_subgraph(self)
        city_planning_subgraph = build_city_planning_subgraph(self)
        enrichment_subgraph = build_enrichment_subgraph(self)
        quality_subgraph = build_quality_subgraph(self)
        builder = StateGraph(
            TravelPlanningGraphState,
            context_schema=PlanningContext,
        )
        builder.add_node("requirement_resolution", requirement_subgraph)
        builder.add_node("city_planning", city_planning_subgraph)
        builder.add_node("itinerary_enrichment", enrichment_subgraph)
        builder.add_node("quality_control", quality_subgraph)
        builder.add_edge(START, "requirement_resolution")
        builder.add_edge("requirement_resolution", "city_planning")
        builder.add_edge("city_planning", "itinerary_enrichment")
        builder.add_edge("itinerary_enrichment", "quality_control")
        builder.add_edge("quality_control", END)
        return builder.compile(checkpointer=checkpointer)

    async def run(
        self,
        requirement,
        *,
        progress=None,
        thread_id: str | None = None,
        resume_value=None,
        interrupt_for_destination_choice: bool | None = None,
    ):
        """Run a plan, discarding stream events for synchronous callers."""

        async for event in self.astream(
            requirement,
            progress=progress,
            thread_id=thread_id,
            resume_value=resume_value,
            interrupt_for_destination_choice=interrupt_for_destination_choice,
        ):
            if event.get("type") == "plan_completed":
                return event["result"]
        raise RuntimeError("LangGraph 规划流程未返回最终结果")

    async def astream(
        self,
        requirement,
        *,
        progress=None,
        thread_id: str | None = None,
        resume_value=None,
        interrupt_for_destination_choice: bool | None = None,
    ):
        """Yield normalized workflow events directly from LangGraph.

        Nodes emit custom events through ``Runtime.stream_writer``. The
        application can persist exactly these events for SSE replay, so the
        database is no longer a second, independently-authored event source.
        """

        thread_id = thread_id or uuid4().hex
        input_state = {
            "requirement": requirement,
            "planning_retry_count": 0,
        }
        config = {"configurable": {"thread_id": thread_id}}
        event_queue = asyncio.Queue()
        done_marker = object()

        async def report(step: str, label: str, status: str) -> None:
            await event_queue.put(
                (
                    "event",
                    {
                        "type": "planning_progress",
                        "step": step,
                        "label": label,
                        "status": status,
                    },
                )
            )
            if progress is not None:
                result = progress(step, label, status)
                if isawaitable(result):
                    await result

        context = {
            # The queue is the canonical event source for both direct and
            # nested subgraph nodes. ``progress`` remains a compatibility
            # callback, not a second event publisher.
            "progress": report,
            "use_interrupts": (
                bool(self.checkpoint_path)
                if interrupt_for_destination_choice is None
                else interrupt_for_destination_choice
            ),
        }

        async def stream(graph):
            latest_state = None
            graph_input = (
                Command(resume=resume_value)
                if resume_value is not None
                else input_state
            )
            async def consume_graph():
                nonlocal latest_state
                try:
                    async for chunk in graph.astream(
                        graph_input,
                        config=config,
                        context=context,
                        stream_mode=["custom", "values"],
                    ):
                        mode, payload = chunk
                        if mode == "custom":
                            await event_queue.put(("event", payload))
                        elif mode == "values":
                            latest_state = payload
                except BaseException as exc:
                    await event_queue.put(("error", exc))
                else:
                    await event_queue.put(("done", done_marker))

            graph_task = asyncio.create_task(consume_graph())
            try:
                while True:
                    kind, payload = await event_queue.get()
                    if kind == "event":
                        yield payload
                        continue
                    if kind == "error":
                        await graph_task
                        raise payload
                    break
                await graph_task
            finally:
                if not graph_task.done():
                    graph_task.cancel()
                    try:
                        await graph_task
                    except asyncio.CancelledError:
                        pass
            if latest_state is None:
                latest_state = (await graph.aget_state(config)).values
            pending_interrupt = latest_state.get("__interrupt__")
            if pending_interrupt:
                self._raise_destination_choice(pending_interrupt[0].value)
            result = latest_state.get("result")
            if result is None:
                raise RuntimeError("LangGraph 规划流程未返回最终结果")
            yield {"type": "plan_completed", "result": result}

        if self.checkpoint_path:
            checkpoint_path = Path(self.checkpoint_path)
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
                # Planning jobs can run concurrently. WAL plus a bounded busy
                # timeout prevents one worker's checkpoint write from making
                # another worker fail spuriously on SQLite's writer lock.
                await saver.conn.execute("PRAGMA journal_mode=WAL")
                await saver.conn.execute("PRAGMA busy_timeout=5000")
                await saver.conn.commit()
                saver.serde = self._checkpoint_serializer()
                graph = self._build_graph(checkpointer=saver)
                async for event in stream(graph):
                    yield event
        else:
            async for event in stream(self.graph):
                yield event

    @staticmethod
    def _progress(state, runtime: Runtime[PlanningContext] | None):
        if runtime is not None and runtime.context:
            callback = runtime.context.get("progress")
            writer = runtime.stream_writer

            async def emit(step: str, label: str, status: str) -> None:
                if callback is not None:
                    result = callback(step, label, status)
                    if isawaitable(result):
                        await result
                else:
                    writer(
                        {
                            "type": "planning_progress",
                            "step": step,
                            "label": label,
                            "status": status,
                        }
                    )

            return emit
        # Backwards compatibility for direct node tests/integrations that
        # still supply the callback in a raw state dictionary.
        return state.get("progress")

    @staticmethod
    def _checkpoint_serializer():
        """Allow only the domain models intentionally stored in plan state."""

        from backend.models.city_stay import CityStay
        from backend.models.destination import DestinationPlanResult
        from backend.models.degradation import DegradationNotice
        from backend.models.evaluation import (
            DayEvaluationMetric,
            EvaluationIssue,
            EvaluationResult,
        )
        from backend.models.hotel import HotelStay
        from backend.models.itinerary import CityPlan, CityPlanningResult, DayPlan
        from backend.models.requirement import TravelRequirement
        from backend.models.retrieval import HybridSearchResult
        from backend.models.route import RouteSegment
        from backend.models.schedule import DaySchedule
        from backend.models.travel_plan import (
            AttractionStop,
            TravelDay,
            TravelPlan,
            TravelPlanResponse,
        )
        from backend.models.weather import WeatherResult

        model_types = (
            CityStay,
            DestinationPlanResult,
            DegradationNotice,
            DayEvaluationMetric,
            EvaluationIssue,
            EvaluationResult,
            HotelStay,
            CityPlan,
            CityPlanningResult,
            DayPlan,
            TravelRequirement,
            HybridSearchResult,
            RouteSegment,
            DaySchedule,
            AttractionStop,
            TravelDay,
            TravelPlan,
            TravelPlanResponse,
            WeatherResult,
        )
        return maybe_add_typed_methods(
            JsonPlusSerializer(
                allowed_msgpack_modules=[
                    (model.__module__, model.__name__) for model in model_types
                ]
            )
        )

    async def _prepare_requirement(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        requirement = await self.workflow._prepare_requirement(
            state["requirement"],
            progress=self._progress(state, runtime),
        )
        return {"requirement": requirement}

    async def _resolve_destination(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        try:
            requirement, destination = await self.workflow._resolve_destination(
                state["requirement"],
                progress=self._progress(state, runtime),
            )
        except Exception as exc:
            # The legacy application boundary still receives
            # DestinationChoiceRequired. When a durable checkpoint is
            # configured, the same choice is also represented as a native
            # LangGraph interrupt so a caller can resume this exact thread.
            from backend.planner.workflow import DestinationChoiceRequired

            if not isinstance(exc, DestinationChoiceRequired) or not (
                runtime is not None
                and runtime.context
                and runtime.context.get("use_interrupts")
            ):
                raise
            selected_scope = interrupt(
                {
                    "type": "destination_choice",
                    "requirement": exc.requirement.model_dump(mode="json"),
                    "options": exc.options.model_dump(mode="json"),
                }
            )
            if isinstance(selected_scope, dict):
                selected_scope = selected_scope.get("travel_scope") or selected_scope.get("scope")
            if selected_scope not in {"single_city", "multi_city"}:
                raise ValueError("人工选择必须是 single_city 或 multi_city")
            requirement = exc.requirement.model_copy(
                update={"travel_scope": selected_scope}
            )
            requirement, destination = await self.workflow._resolve_destination(
                requirement,
                progress=self._progress(state, runtime),
            )
        return {"requirement": requirement, "destination": destination}

    @staticmethod
    def _raise_destination_choice(payload) -> None:
        from backend.models.destination import DestinationPlanResult
        from backend.models.requirement import TravelRequirement
        from backend.planner.workflow import DestinationChoiceRequired

        if not isinstance(payload, dict):
            raise RuntimeError("LangGraph 返回了无法识别的人工选择中断")
        raise DestinationChoiceRequired(
            TravelRequirement.model_validate(payload["requirement"]),
            DestinationPlanResult.model_validate(payload["options"]),
        )

    async def _plan_cities(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        try:
            city_context = await self.workflow._plan_cities(
                requirement=state["requirement"],
                destination=state["destination"],
                progress=self._progress(state, runtime),
            )
        except CityPlanningError as exc:
            return {
                "planning_error": str(exc),
                "planning_retry_count": state.get("planning_retry_count", 0),
            }
        return {
            "city_context": city_context,
            "planning_error": None,
        }

    @staticmethod
    def _after_plan_cities(state: TravelPlanningGraphState) -> str:
        if not state.get("planning_error"):
            return "continue"
        if state.get("planning_retry_count", 0) == 0:
            return "recover"
        return "fail"

    async def _recover_city_plans(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        progress = self._progress(state, runtime)
        if progress is not None:
            result = progress(
                "planning_recovery",
                "路线校验未能完成，正在使用同一候选池进行本地距离排程",
                "running",
            )
            if isawaitable(result):
                await result
        try:
            city_context = await self.workflow._plan_cities(
                requirement=state["requirement"],
                destination=state["destination"],
                progress=progress,
                allow_route_fallback=True,
            )
        except CityPlanningError as exc:
            return {
                "planning_error": str(exc),
                "planning_retry_count": state.get("planning_retry_count", 0) + 1,
            }
        if progress is not None:
            result = progress(
                "planning_recovery",
                "已完成路线降级排程",
                "completed",
            )
            if isawaitable(result):
                await result
        return {
            "city_context": city_context,
            "planning_error": None,
            "planning_retry_count": state.get("planning_retry_count", 0) + 1,
        }

    @staticmethod
    def _after_recovery(state: TravelPlanningGraphState) -> str:
        return "fail" if state.get("planning_error") else "continue"

    @staticmethod
    def _planning_failed(state: TravelPlanningGraphState):
        raise CityPlanningError(
            state.get("planning_error") or "城市景点规划失败"
        )

    def _assemble_itinerary(self, state: TravelPlanningGraphState):
        plan = self.workflow._assemble_itinerary(
            requirement=state["requirement"],
            destination=state["destination"],
            city_context=state["city_context"],
        )
        return {"plan": plan}

    async def _enrich_hotels(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        plan = await self.workflow._enrich_hotels(
            requirement=state["requirement"],
            plan=state["plan"],
            progress=self._progress(state, runtime),
        )
        return {"plan": plan}

    async def _enrich_weather_and_routes(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        plan = await self.workflow._enrich_weather_and_routes(
            requirement=state["requirement"],
            plan=state["plan"],
            city_context=state["city_context"],
            progress=self._progress(state, runtime),
        )
        return {"plan": plan}

    async def _evaluate_and_persist(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        result = await self.workflow._evaluate_and_finalize(
            requirement=state["requirement"],
            plan=state["plan"],
            progress=self._progress(state, runtime),
        )
        return {"result": result}

    async def _review_plan(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        review = await self.workflow._review_plan(
            response=state["result"],
            progress=self._progress(state, runtime),
        )
        return {"review": review}

    async def _repair_plan(
        self,
        state: TravelPlanningGraphState,
        runtime: Runtime[PlanningContext] | None = None,
    ):
        response = await self.workflow._repair_plan(
            response=state["result"],
            review=state.get("review") or {},
            progress=self._progress(state, runtime),
        )
        return {"result": response}


__all__ = ["TravelPlanningGraph", "TravelPlanningGraphState"]
