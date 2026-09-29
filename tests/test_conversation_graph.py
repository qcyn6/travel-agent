from backend.conversation.graph import PlanningConversationGraph


def test_planning_conversation_graph_has_explicit_conditional_routes():
    graph = PlanningConversationGraph(object()).graph
    nodes = set(graph.nodes)
    assert {
        "prepare",
        "route",
        "mode_boundary",
        "validation_error",
        "destination_error",
        "invite_more",
        "general_reply",
        "planning_reply",
        "finalize",
    } <= nodes

    mermaid = graph.get_graph().draw_mermaid()
    assert "route -.-> planning_reply" in mermaid
    assert "route -.-> validation_error" in mermaid
    assert "finalize --> __end__" in mermaid
