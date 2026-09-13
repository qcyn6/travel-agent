from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class TravelRefinementState(TypedDict, total=False):
    plan: object
    requirement: object
    candidates_by_city: dict
    evaluation: object
    replan_count: int
    stopped_no_change: bool
    progress: object


class PlanRefinementGraph:
    def __init__(
        self,
        *,
        evaluator,
        replanning_service,
        refresh_changed_days,
        max_replan_count: int,
        emit,
    ):
        self.evaluator = evaluator
        self.replanning_service = replanning_service
        self.refresh_changed_days = refresh_changed_days
        self.max_replan_count = max(0, max_replan_count)
        self.emit = emit
        builder = StateGraph(TravelRefinementState)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("replan", self._replan)
        builder.add_edge(START, "evaluate")
        builder.add_conditional_edges("evaluate", self._next_step)
        builder.add_edge("replan", "evaluate")
        self.graph = builder.compile()

    async def run(self, *, plan, requirement, candidates_by_city, progress=None):
        state = await self.graph.ainvoke(
            {
                "plan": plan,
                "requirement": requirement,
                "candidates_by_city": candidates_by_city,
                "replan_count": 0,
                "stopped_no_change": False,
                "progress": progress,
            },
            {"recursion_limit": self.max_replan_count * 2 + 5},
        )
        evaluation = state["evaluation"]
        count = state["replan_count"]
        limit_reached = evaluation.replan_required and count >= self.max_replan_count
        return state["plan"].model_copy(
            update={
                "evaluation": evaluation,
                "replan_count": count,
                "replan_limit_reached": limit_reached,
            }
        )

    async def _evaluate(self, state):
        await self.emit(state.get("progress"), "evaluation", "正在检查行程可执行性", "running")
        evaluation = self.evaluator.evaluate(state["plan"], state["requirement"])
        plan = state["plan"].model_copy(update={"evaluation": evaluation})
        await self.emit(state.get("progress"), "evaluation", "行程可执行性检查完成", "completed")
        return {"plan": plan, "evaluation": evaluation}

    def _next_step(self, state):
        if (
            state["evaluation"].replan_required
            and state["replan_count"] < self.max_replan_count
            and not state["stopped_no_change"]
        ):
            return "replan"
        return END

    async def _replan(self, state):
        count = state["replan_count"] + 1
        progress = state.get("progress")
        await self.emit(progress, "replanning", f"正在进行第 {count} 次局部行程调整", "running")
        replanned = await self.replanning_service.replan(
            plan=state["plan"],
            evaluation=state["evaluation"],
            requirement=state["requirement"],
            candidates_by_city=state["candidates_by_city"],
        )
        if not replanned.changed_days:
            await self.emit(progress, "replanning", "没有可安全应用的局部调整", "completed")
            return {"plan": replanned.plan, "replan_count": count, "stopped_no_change": True}
        plan = await self.refresh_changed_days(
            replanned.plan,
            changed_days=replanned.changed_days,
            requested_mode=state["requirement"].transport_mode,
        )
        await self.emit(progress, "replanning", f"第 {count} 次局部调整已应用", "completed")
        return {"plan": plan, "replan_count": count, "stopped_no_change": False}
