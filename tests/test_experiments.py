import json

import pytest

from backend.planner.dynamic_scheduler import calculate_priority
from backend.models.itinerary import CityPlan, DayPlan
from experiments.ablation import run_retrieval_ablation, write_retrieval_ablation_results
from experiments.cases import make_experiment_cases
from experiments.baselines import (
    LLMBaselineAdapter,
    run_baseline,
    run_baseline_matrix,
    write_baseline_results,
)
from experiments.runner import run_matrix, run_variant, write_results
from experiments.tool_acceptance import run_fixture_acceptance
from experiments.variants import EXPERIMENT_VARIANTS, RETRIEVAL_ABLATION_VARIANTS


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


@pytest.mark.asyncio
async def test_injected_llm_baseline_is_scored_against_the_same_candidate_pool(tmp_path):
    case = make_experiment_cases()[0]

    class Planner:
        async def plan(self, *, city_stay, requirement, candidates):
            del requirement
            return CityPlan(
                city=city_stay.city,
                days=[
                    DayPlan(
                        day=1,
                        attractions=[candidate.name for candidate in candidates[:2]],
                        description="固定测试基线只使用候选池中的景点。",
                    )
                ],
            )

    result = await run_baseline(case, Planner(), source="fixture")
    assert result.source == "fixture"
    assert result.metrics["valid_attraction_count"] == 2
    assert result.metrics["unknown_attraction_count"] == 0
    assert result.metrics["feasible"] is True

    paths = write_baseline_results([result], tmp_path)
    assert "llm_direct" in paths["json"].read_text(encoding="utf-8")
    assert "LLM 直接生成 Baseline 结果" in paths["markdown"].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_baseline_marks_unknown_and_duplicate_names_as_infeasible():
    case = make_experiment_cases()[0]

    class InvalidPlanner:
        async def plan(self, **_kwargs):
            return CityPlan(
                city="杭州市",
                days=[
                    DayPlan(
                        day=1,
                        attractions=["杭州市中心湖景", "杭州市中心湖景", "虚构景点"],
                        description="这个测试计划故意包含重复和未知景点。",
                    )
                ],
            )

    result = await run_baseline(case, InvalidPlanner(), source="fixture")
    assert result.metrics["duplicate_attraction_count"] == 1
    assert result.metrics["unknown_attraction_count"] == 1
    assert result.metrics["feasible"] is False


@pytest.mark.asyncio
async def test_baseline_adapter_runs_fixed_matrix_with_injected_planner():
    class Planner:
        async def plan(self, *, city_stay, requirement, candidates):
            del requirement
            return CityPlan(
                city=city_stay.city,
                days=[
                    DayPlan(
                        day=day,
                        attractions=[candidates[day - 1].name],
                        description="每一天只安排一个固定候选景点，便于验证基线。",
                    )
                    for day in range(1, city_stay.days + 1)
                ],
            )

    results = await run_baseline_matrix(
        make_experiment_cases()[:1],
        LLMBaselineAdapter(Planner(), source="fixture"),
    )
    assert len(results) == 1
    assert results[0].source == "fixture"


@pytest.mark.asyncio
async def test_fixture_tool_acceptance_covers_success_and_degradation_paths():
    results = await run_fixture_acceptance()
    by_case = {result.case_id: result for result in results}
    assert {result.status for result in results} == {"pass"}
    assert by_case["route-success"].source == "amap_mcp"
    assert by_case["route-estimated-fallback"].source == "haversine"
    assert by_case["route-missing-coordinates"].source == "unavailable"
    assert by_case["weather-success"].source == "qweather"
    assert by_case["weather-unavailable-fallback"].source == "none"


def test_experiment_candidates_keep_independent_semantic_and_keyword_scores():
    candidates = make_experiment_cases()[0].cities[0].candidates
    assert all(candidate.semantic_score is not None for candidate in candidates)
    assert any(
        candidate.semantic_score != candidate.keyword_score for candidate in candidates
    )
    assert all(0 <= candidate.preference_score <= 1 for candidate in candidates)


@pytest.mark.asyncio
async def test_retrieval_ablation_uses_one_scheduler_and_separates_selection_order(tmp_path):
    case = make_experiment_cases()[0]
    results = await run_retrieval_ablation([case], RETRIEVAL_ABLATION_VARIANTS)
    assert [result.variant_id for result in results] == [
        "semantic_only",
        "keyword_only",
        "semantic_keyword_hybrid",
    ]
    assert all(result.metrics["feasible"] is True for result in results)
    assert results[0].metrics["selection_signature"] != results[1].metrics[
        "selection_signature"
    ]
    assert results[1].metrics["route_minutes"] < results[0].metrics["route_minutes"]
    assert results[2].semantic_weight == pytest.approx(0.7)
    assert results[2].keyword_weight == pytest.approx(0.3)

    paths = write_retrieval_ablation_results(results, tmp_path)
    assert paths["json"].name == "retrieval_ablation.json"
    assert "semantic_weight" in paths["csv"].read_text(encoding="utf-8-sig").splitlines()[0]
    assert "检索权重消融实验结果" in paths["markdown"].read_text(encoding="utf-8")
