"""LangGraph orchestration for one planning conversation turn.

The graph deliberately keeps business validation and persistence in
``ConversationService``.  LangGraph owns the turn-level routing so the API
and the non-streaming service path use the same conversation flow.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.conversation.responder import ConversationResponseError


class ConversationGraphState(TypedDict, total=False):
    conversation_id: str
    user_message: str
    mode: str | None
    prepared: Any
    decision: Any
    reply: str
    conversation: Any
    suggestions: list[str]
    route: str


class PlanningConversationGraph:
    """Run one planning conversation turn through an explicit state graph.

    The graph is intentionally created without a checkpointer in this first
    migration step.  MySQL remains the source of truth for conversation state;
    each request loads that state and executes one graph turn.  A persistent
    LangGraph checkpointer can be added later without changing the node
    contracts.
    """

    def __init__(self, service):
        self.service = service
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(ConversationGraphState)
        builder.add_node("prepare", self._prepare)
        builder.add_node("route", self._route)
        builder.add_node("mode_boundary", self._mode_boundary)
        builder.add_node("validation_error", self._validation_error)
        builder.add_node("destination_error", self._destination_error)
        builder.add_node("invite_more", self._invite_more)
        builder.add_node("general_reply", self._general_reply)
        builder.add_node("planning_reply", self._planning_reply)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "prepare")
        builder.add_edge("prepare", "route")
        builder.add_conditional_edges(
            "route",
            self._route_name,
            {
                "mode_boundary": "mode_boundary",
                "validation_error": "validation_error",
                "destination_error": "destination_error",
                "invite_more": "invite_more",
                "general_reply": "general_reply",
                "planning_reply": "planning_reply",
            },
        )
        for node in (
            "mode_boundary",
            "validation_error",
            "destination_error",
            "invite_more",
            "general_reply",
            "planning_reply",
        ):
            builder.add_edge(node, "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()

    async def run(
        self,
        conversation_id: str,
        message: str,
        *,
        mode: str | None = None,
    ) -> tuple[Any, str, list[str], Any]:
        state = await self.graph.ainvoke(
            {
                "conversation_id": conversation_id,
                "user_message": message,
                "mode": mode,
            }
        )
        return (
            state["conversation"],
            state["reply"],
            state["suggestions"],
            state["prepared"],
        )

    async def _prepare(self, state: ConversationGraphState) -> ConversationGraphState:
        prepared = await self.service.prepare_message(
            state["conversation_id"],
            state["user_message"],
            mode=state.get("mode"),
        )
        return {"prepared": prepared}

    @staticmethod
    def _route(state: ConversationGraphState) -> ConversationGraphState:
        prepared = state["prepared"]
        if prepared.mode_boundary:
            route = "mode_boundary"
        elif prepared.validation_error:
            route = "validation_error"
        elif prepared.destination_error:
            route = "destination_error"
        elif prepared.should_invite_more:
            route = "invite_more"
        elif prepared.intent == "general":
            route = "general_reply"
        else:
            route = "planning_reply"
        return {"route": route}

    @staticmethod
    def _route_name(state: ConversationGraphState) -> Literal[
        "mode_boundary",
        "validation_error",
        "destination_error",
        "invite_more",
        "general_reply",
        "planning_reply",
    ]:
        return state["route"]  # type: ignore[return-value]

    @staticmethod
    def _mode_boundary(state: ConversationGraphState) -> ConversationGraphState:
        return {"reply": state["prepared"].mode_boundary}

    @staticmethod
    def _validation_error(state: ConversationGraphState) -> ConversationGraphState:
        return {"reply": state["prepared"].validation_error}

    @staticmethod
    def _destination_error(state: ConversationGraphState) -> ConversationGraphState:
        prepared = state["prepared"]
        return {
            "reply": (
                f"{prepared.destination_error}。换一个已收录的国内省份或城市试试，"
                "也可以让我根据你的兴趣和偏好推荐一个省份。"
            )
        }

    def _invite_more(self, state: ConversationGraphState) -> ConversationGraphState:
        return {"reply": self.service.more_details_reply(state["prepared"].requirement)}

    async def _general_reply(self, state: ConversationGraphState) -> ConversationGraphState:
        prepared = state["prepared"]
        try:
            response_inputs = self.service.responder_inputs(prepared)
            reply = await self.service.responder.respond(
                user_message=prepared.user_message,
                **response_inputs,
                recent_messages=prepared.recent_messages,
                conversation_mode="general",
            )
        except ConversationResponseError:
            reply = self.service._general_fallback_reply(
                prepared.user_message,
                prepared.destination_context,
            )
        return {"reply": reply}

    async def _planning_reply(self, state: ConversationGraphState) -> ConversationGraphState:
        decision = await self.service.planning_decision(state["prepared"])
        return {"reply": decision.reply, "decision": decision}

    def _finalize(self, state: ConversationGraphState) -> ConversationGraphState:
        prepared = state["prepared"]
        reply = state["reply"]
        conversation = self.service.finish_message(prepared, reply)
        decision = state.get("decision")
        if decision is not None and prepared.intent == "planning":
            suggestions = self.service.suggestions_for_decision(prepared, decision)
        else:
            suggestions = self.service.suggestions_for_reply(prepared, reply)
        if prepared.validation_error and prepared.intent == "planning":
            suggestions = self.service.validation_suggestions(
                prepared.validation_error,
                prepared.requirement,
            )
        return {
            "conversation": conversation,
            "suggestions": suggestions,
        }


__all__ = ["ConversationGraphState", "PlanningConversationGraph"]
