"""Composable LangGraph subgraphs for the travel-planning workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from backend.planner.state import PlanningContext, PlanningState


def build_requirement_subgraph(owner):
    """Resolve and validate the confirmed travel requirement."""

    builder = StateGraph(PlanningState, context_schema=PlanningContext)
    builder.add_node("prepare_requirement", owner._prepare_requirement)
    builder.add_node("resolve_destination", owner._resolve_destination)
    builder.add_edge(START, "prepare_requirement")
    builder.add_edge("prepare_requirement", "resolve_destination")
    builder.add_edge("resolve_destination", END)
    return builder.compile()


def build_city_planning_subgraph(owner):
    """Plan cities and keep the bounded route-fallback branch local."""

    builder = StateGraph(PlanningState, context_schema=PlanningContext)
    builder.add_node("plan_cities", owner._plan_cities)
    builder.add_node("recover_city_plans", owner._recover_city_plans)
    builder.add_node("planning_failed", owner._planning_failed)
    builder.add_edge(START, "plan_cities")
    builder.add_conditional_edges(
        "plan_cities",
        owner._after_plan_cities,
        {
            "continue": END,
            "recover": "recover_city_plans",
            "fail": "planning_failed",
        },
    )
    builder.add_conditional_edges(
        "recover_city_plans",
        owner._after_recovery,
        {
            "continue": END,
            "fail": "planning_failed",
        },
    )
    builder.add_edge("planning_failed", END)
    return builder.compile()


def build_enrichment_subgraph(owner):
    """Assemble the itinerary and enrich hotels, routes, weather, and schedule."""

    builder = StateGraph(PlanningState, context_schema=PlanningContext)
    builder.add_node("assemble_itinerary", owner._assemble_itinerary)
    builder.add_node("enrich_hotels", owner._enrich_hotels)
    builder.add_node("enrich_weather_and_routes", owner._enrich_weather_and_routes)
    builder.add_edge(START, "assemble_itinerary")
    builder.add_edge("assemble_itinerary", "enrich_hotels")
    builder.add_edge("enrich_hotels", "enrich_weather_and_routes")
    builder.add_edge("enrich_weather_and_routes", END)
    return builder.compile()


def build_quality_subgraph(owner):
    """Evaluate, review, and safely repair only presentation-level issues."""

    builder = StateGraph(PlanningState, context_schema=PlanningContext)
    builder.add_node("evaluate_and_persist", owner._evaluate_and_persist)
    builder.add_node("review_plan", owner._review_plan)
    builder.add_node("repair_plan", owner._repair_plan)
    builder.add_edge(START, "evaluate_and_persist")
    builder.add_edge("evaluate_and_persist", "review_plan")
    builder.add_edge("review_plan", "repair_plan")
    builder.add_edge("repair_plan", END)
    return builder.compile()


__all__ = [
    "build_city_planning_subgraph",
    "build_enrichment_subgraph",
    "build_quality_subgraph",
    "build_requirement_subgraph",
]
