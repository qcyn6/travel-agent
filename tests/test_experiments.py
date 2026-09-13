import asyncio
from datetime import date
from types import SimpleNamespace

from backend.experiments import (
    DeepSeekBaselinePlanner,
    EXPERIMENT_CASES,
    evaluate_baseline_plan,
    evaluate_system_plan,
)
from backend.models.experiment import BaselinePlan, ExperimentCase
from backend.models.attraction_facts import OpeningHours, OpeningPeriod


def test_experiment_dataset_has_24_balanced_unique_cases():
    assert len(EXPERIMENT_CASES) == 24
    assert len({case.id for case in EXPERIMENT_CASES}) == 24
    assert sum(case.group == "single_day" for case in EXPERIMENT_CASES) == 8
    assert sum(case.group == "single_city_multi_day" for case in EXPERIMENT_CASES) == 8
    assert sum(case.group == "province_multi_city" for case in EXPERIMENT_CASES) == 8
    assert all(case.interests for case in EXPERIMENT_CASES)
    assert EXPERIMENT_CASES[8].interests == ["自然", "人文"]


def test_deepseek_baseline_parses_strict_json():
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '```json\n{"days":[{"day":1,"city":"杭州市",'
                                '"attractions":[{"name":"西湖","estimated_visit_minutes":180}]}]}\n```'
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResponse()

    case = ExperimentCase(
        id="SD-01",
        group="single_day",
        text="去杭州玩一天",
        travel_scope="single_city",
        days=1,
        interests=["历史"],
    )
    planner = DeepSeekBaselinePlanner(
        "test-key", base_url="https://example.test", model="test", client=FakeClient()
    )

    plan = asyncio.run(planner.plan(case))

    assert plan.days[0].attractions[0].name == "西湖"


def test_baseline_metrics_are_scored_without_external_facts():
    case = ExperimentCase(
        id="SD-01",
        group="single_day",
        text="去杭州玩一天",
        travel_scope="single_city",
        days=1,
        interests=["历史", "摄影"],
    )
    plan = BaselinePlan.model_validate(
        {
            "days": [
                {
                    "day": 1,
                    "city": "杭州市",
                    "attractions": [
                        {"name": "西湖", "estimated_visit_minutes": 180},
                        {"name": "历史摄影馆", "estimated_visit_minutes": 180},
                    ],
                }
            ]
        }
    )

    metrics = evaluate_baseline_plan(
        plan,
        case,
        attraction_exists=lambda city, name: name == "西湖",
    )

    assert metrics.attraction_validity == 0.5
    assert metrics.interest_coverage == 1
    assert metrics.schedule_feasibility == 1
    assert metrics.route_reasonableness is None
    assert metrics.external_constraint_satisfaction is None


def test_system_metrics_use_plan_facts_instead_of_internal_evaluator_result():
    opening = OpeningHours(
        status="known",
        periods=[OpeningPeriod(open_time="08:00", close_time="18:00")],
        confidence="high",
    )
    attractions = [
        SimpleNamespace(
            poi_id="A1",
            city="杭州市",
            longitude=120.1,
            latitude=30.2,
            name="历史文化博物馆",
            category="博物馆",
            description="适合摄影",
            environment="indoor",
            opening_hours=opening,
            start_time="09:00",
        ),
        SimpleNamespace(
            poi_id="A2",
            city="杭州市",
            longitude=120.2,
            latitude=30.3,
            name="公园",
            category="风景名胜",
            description=None,
            environment="outdoor",
            opening_hours=None,
            start_time="11:00",
        ),
    ]
    day = SimpleNamespace(
        day=1,
        date=date(2026, 9, 15),
        city="杭州市",
        attractions=attractions,
        routes=[SimpleNamespace(duration_min=30)],
        schedule=SimpleNamespace(total_minutes=500),
        weather=SimpleNamespace(
            status="available", condition="晴", precipitation_probability=0.1
        ),
    )
    result = SimpleNamespace(
        plan=SimpleNamespace(
            itinerary=[day],
            evaluation=SimpleNamespace(
                issues=[SimpleNamespace(type="weather", severity="high", day=1)]
            ),
        )
    )
    case = ExperimentCase(
        id="SD-01",
        group="single_day",
        text="去杭州玩一天",
        travel_scope="single_city",
        days=1,
        interests=["历史", "摄影"],
    )

    metrics = evaluate_system_plan(
        result,
        case,
        attraction_exists=lambda city, name: city == "杭州市" and bool(name),
    )

    assert metrics.attraction_validity == 1
    assert metrics.interest_coverage == 1
    assert metrics.schedule_feasibility == 1
    assert metrics.route_reasonableness == 1
    assert metrics.external_constraint_satisfaction == 1
