from types import SimpleNamespace

import pytest

from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.replanning import DayReplanDecision
from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult
from backend.models.schedule import DaySchedule
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan
from backend.replanner.deepseek import DeepSeekDayReplanner, ReplannerError
from backend.replanner.service import ReplanningService


def _candidate(poi_id, name, rank):
    return HybridSearchResult(
        poi_id=poi_id, name=name, city="杭州市", category="风景名胜",
        longitude=120 + rank / 100, latitude=30 + rank / 100,
        description=f"{name}是一处用于测试局部重规划的候选景点资料。",
        rrf_score=1 / rank, final_rank=rank,
    )


def _fixture_plan():
    stops = [
        AttractionStop(
            poi_id=f"A{index}", name=f"景点{index}", city="杭州市",
            estimated_visit_minutes=240, duration_source="category_fallback",
        )
        for index in range(1, 4)
    ]
    schedule = DaySchedule(
        start_time="09:00", end_time="23:30", visit_minutes=720, route_minutes=60,
        meal_minutes=90, buffer_minutes=60, total_minutes=930, status="estimated",
    )
    days = [
        TravelDay(
            day=1, city="杭州市", city_adcode="330100", city_day=1,
            attractions=stops, schedule=schedule, description="第一天原始行程安排说明。",
        ),
        TravelDay(
            day=2, city="杭州市", city_adcode="330100", city_day=2,
            attractions=[AttractionStop(poi_id="B1", name="正常景点", city="杭州市")],
            description="第二天保持不变的行程说明。",
        ),
    ]
    return TravelPlan(
        province="浙江省",
        city_stays=[{"city": "杭州市", "days": 2}],
        city_plans=[CityPlan(city="杭州市", days=[
            DayPlan(day=1, attractions=[stop.name for stop in stops], description=days[0].description),
            DayPlan(day=2, attractions=["正常景点"], description=days[1].description),
        ])],
        itinerary=days,
    )


def _evaluation():
    return EvaluationResult(
        passed=False,
        replan_required=True,
        issues=[EvaluationIssue(
            type="schedule_density", severity="high", day=1,
            message="第 1 天行程过密", suggestion="减少景点",
        )],
    )


class DecisionReplanner:
    async def replan_day(self, **kwargs):
        return DayReplanDecision(
            day=1, attraction_poi_ids=["A2", "A1"], description="只调整第一天并保留两个核心景点。"
        )


class FailingReplanner:
    async def replan_day(self, **kwargs):
        raise ReplannerError("模型不可用")


@pytest.mark.asyncio
async def test_replanning_changes_only_problem_day_and_syncs_city_plan():
    original = _fixture_plan()
    candidates = [_candidate(f"A{index}", f"景点{index}", index) for index in range(1, 4)]

    result = await ReplanningService(DecisionReplanner()).replan(
        plan=original,
        evaluation=_evaluation(),
        requirement=TravelRequirement(cities=["杭州市"], days=2, travel_scope="single_city"),
        candidates_by_city={"杭州市": candidates},
    )

    assert result.changed_days == [1]
    assert [stop.poi_id for stop in result.plan.itinerary[0].attractions] == ["A2", "A1"]
    assert result.plan.itinerary[1] == original.itinerary[1]
    assert result.plan.city_plans[0].days[0].attractions == ["景点2", "景点1"]
    assert result.plan.evaluation is None


@pytest.mark.asyncio
async def test_replanning_has_conservative_fallback():
    result = await ReplanningService(FailingReplanner()).replan(
        plan=_fixture_plan(),
        evaluation=_evaluation(),
        requirement=TravelRequirement(cities=["杭州市"], days=2, travel_scope="single_city"),
        candidates_by_city={"杭州市": []},
    )

    assert result.used_fallback is True
    assert result.changed_days == [1]
    assert len(result.plan.itinerary[0].attractions) == 1


class FakeResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": (
            '{"day":1,"attraction_poi_ids":["A2","A1"],'
            '"description":"调整顺序并减少当天的路线冲突。"}'
        )}}]}


class FakeClient:
    async def post(self, *args, **kwargs):
        return FakeResponse()


@pytest.mark.asyncio
async def test_deepseek_replanner_accepts_only_candidate_poi_ids():
    planner = DeepSeekDayReplanner(
        "test-key", base_url="https://example.com", model="test-model", client=FakeClient()
    )
    plan = _fixture_plan()
    candidates = [_candidate("A1", "景点1", 1), _candidate("A2", "景点2", 2)]

    result = await planner.replan_day(
        day=plan.itinerary[0], issues=_evaluation().issues, candidates=candidates,
        requirement=TravelRequirement(cities=["杭州市"], days=2, travel_scope="single_city"),
    )

    assert result.attraction_poi_ids == ["A2", "A1"]
