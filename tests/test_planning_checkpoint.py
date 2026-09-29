import pytest

from backend.models.city_stay import CityStay
from backend.models.destination import DestinationPlanResult
from backend.models.requirement import TravelRequirement
from backend.planner.graph import TravelPlanningGraph
from backend.planner.workflow import DestinationChoiceRequired


class ChoiceWorkflow:
    async def _prepare_requirement(self, requirement, *, progress=None):
        if progress is not None:
            await progress("requirement", "需求已确认", "completed")
        return requirement

    async def _resolve_destination(self, requirement, *, progress=None):
        if requirement.travel_scope == "auto":
            raise DestinationChoiceRequired(
                requirement,
                DestinationPlanResult(
                    city_stays=[CityStay(city="杭州市", days=2)],
                    alternative_city_stays=[
                        CityStay(city="杭州市", days=1),
                        CityStay(city="宁波市", days=1),
                    ],
                    requires_choice=True,
                ),
            )
        return requirement, DestinationPlanResult(
            city_stays=[CityStay(city="杭州市", days=2)]
        )

    async def _plan_cities(self, **kwargs):
        return {"city_plans": [], "city_results": []}

    def _assemble_itinerary(self, **kwargs):
        return {"itinerary": []}

    async def _enrich_hotels(self, *, plan, **kwargs):
        return plan

    async def _enrich_weather_and_routes(self, *, plan, **kwargs):
        return plan

    async def _evaluate_and_finalize(self, **kwargs):
        return {"completed": True}

    async def _review_plan(self, **kwargs):
        return {"status": "skipped", "approved": True}

    async def _repair_plan(self, *, response, **kwargs):
        return response


@pytest.mark.asyncio
async def test_checkpointed_graph_resumes_destination_choice(tmp_path):
    graph = TravelPlanningGraph(
        ChoiceWorkflow(),
        checkpoint_path=str(tmp_path / "planning-checkpoints.sqlite3"),
    )
    requirement = TravelRequirement(days=2)

    with pytest.raises(DestinationChoiceRequired) as caught:
        await graph.run(requirement, thread_id="choice-job")

    assert caught.value.options.requires_choice is True
    result = await graph.run(
        requirement,
        thread_id="choice-job",
        resume_value="single_city",
    )

    assert result == {"completed": True}


@pytest.mark.asyncio
async def test_graph_stream_exposes_custom_progress_events(tmp_path):
    graph = TravelPlanningGraph(
        ChoiceWorkflow(),
        checkpoint_path=str(tmp_path / "planning-events.sqlite3"),
    )
    events = [
        event
        async for event in graph.astream(
            TravelRequirement(days=2, travel_scope="single_city"),
            thread_id="event-job",
        )
    ]

    assert events[0] == {
        "type": "planning_progress",
        "step": "requirement",
        "label": "需求已确认",
        "status": "completed",
    }
    assert events[-1]["type"] == "plan_completed"
