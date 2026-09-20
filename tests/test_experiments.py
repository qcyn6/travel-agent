import json

import pytest

from backend.planner.dynamic_scheduler import calculate_priority
from experiments.cases import make_experiment_cases
from experiments.runner import run_matrix, run_variant, write_results
from experiments.variants import EXPERIMENT_VARIANTS


def test_experiment_matrix_contains_product_and_failure_cases():
    cases = make_experiment_cases()
    case_ids = {case.case_id for case in cases}

    assert {
        "hangzhou-1d",
        "beijing-2d",
        "chengdu-3d",
        "yunnan-4d",
        "yunnan-7d",
        "hangzhou-short-window",
        "hangzhou-route-unavailable",
        "hangzhou-estimated-route",
    } <= case_ids
    assert sum(city.city_stay.days for city in next(case for case in cases if case.case_id == "yunnan-7d").cities) == 7


def test_priority_weights_keep_production_default_and_allow_ablation():
    assert calculate_priority(0.8, 0.2) == pytest.approx(0.62)
    assert calculate_priority(0.8, 0.2, preference_weight=1.0, distance_weight=0.0) == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_complete_variant_reduces_route_minutes_in_fixed_case():
    cases = make_experiment_cases()
    case = next(case for case in cases if case.case_id == "hangzhou-1d")
    baseline = await run_variant(case, EXPERIMENT_VARIANTS[0])
    balanced = await run_variant(case, EXPERIMENT_VARIANTS[1])

    assert balanced.metrics["preference_coverage"] == 1.0
    assert balanced.metrics["route_minutes"] < baseline.metrics["route_minutes"]
    assert balanced.metrics["feasible"] is True


@pytest.mark.asyncio
async def test_failure_cases_expose_rejections_and_estimated_routes():
    cases = make_experiment_cases()
    unavailable_case = next(case for case in cases if case.case_id == "hangzhou-route-unavailable")
    estimated_case = next(case for case in cases if case.case_id == "hangzhou-estimated-route")

    unavailable = await run_variant(unavailable_case, EXPERIMENT_VARIANTS[0])
    estimated = await run_variant(estimated_case, EXPERIMENT_VARIANTS[0])

    assert unavailable.metrics["route_unavailable_rejections"] >= 1
    assert estimated.metrics["estimated_route_ratio"] > 0


@pytest.mark.asyncio
async def test_matrix_and_outputs_are_reproducible_in_shape(tmp_path):
    case = make_experiment_cases()[0]
    results = await run_matrix([case], EXPERIMENT_VARIANTS)
    paths = write_results(results, tmp_path)

    assert len(results) == 3
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert [item["variant_id"] for item in payload] == [
        "preference_only",
        "balanced",
        "distance_heavy",
    ]
    assert "route_minutes" in paths["csv"].read_text(encoding="utf-8-sig").splitlines()[0]
    assert "规划算法离线实验结果" in paths["markdown"].read_text(encoding="utf-8")
