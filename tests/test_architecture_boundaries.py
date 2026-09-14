from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_domain_models_do_not_import_destination_planner():
    travel_plan_source = (ROOT / "backend/models/travel_plan.py").read_text(encoding="utf-8")
    assert "backend.destination.planner" not in travel_plan_source


def test_conversation_repository_does_not_own_travel_plan_queries():
    repository_source = (ROOT / "backend/conversation/repository.py").read_text(encoding="utf-8")
    assert "TravelPlanRow" not in repository_source
