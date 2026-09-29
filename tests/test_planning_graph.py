from backend.planner.graph import TravelPlanningGraph


def test_travel_planning_graph_contains_generation_stages():
    graph = TravelPlanningGraph(object()).graph
    nodes = set(graph.nodes)
    assert {
        "requirement_resolution",
        "city_planning",
        "itinerary_enrichment",
        "quality_control",
    } <= nodes

    mermaid = graph.get_graph().draw_mermaid()
    assert "requirement_resolution --> city_planning" in mermaid
    assert "city_planning --> itinerary_enrichment" in mermaid
    assert "itinerary_enrichment --> quality_control" in mermaid
    assert "quality_control --> __end__" in mermaid


def test_travel_planning_graph_retries_route_aware_city_planning_once():
    graph = TravelPlanningGraph(object())

    assert graph._after_plan_cities({"planning_error": "路线不可用", "planning_retry_count": 0}) == "recover"
    assert graph._after_plan_cities({"planning_error": None, "planning_retry_count": 0}) == "continue"
    assert graph._after_plan_cities({"planning_error": "仍然失败", "planning_retry_count": 1}) == "fail"
    assert graph._after_recovery({"planning_error": None}) == "continue"
    assert graph._after_recovery({"planning_error": "仍然失败"}) == "fail"
