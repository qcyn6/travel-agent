"""Baselines and scoring helpers for the planning paper experiments.

The production planner deliberately keeps attraction selection deterministic.
This module provides a separate, opt-in baseline that lets an injected planner
(usually :class:`DeepSeekCityPlanner`) generate a complete ``CityPlan``.  The
baseline is scored against the same frozen candidate and route matrices as the
deterministic variants, so the comparison does not depend on database state.

No network request is made on import.  The live adapter is only constructed by
``build_live_llm_baseline`` and only executed when the experiment CLI receives
``--include-llm-baseline``.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from backend.attractions.dedupe import normalize_attraction_name
from backend.config.settings import Settings, get_settings
from backend.models.itinerary import CityPlan
from backend.models.planning import PlanningCandidate
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.planner.city import DeepSeekCityPlanner
from backend.schedule import ScheduleBuilder

from experiments.cases import ExperimentCase, ExperimentCityCase, make_experiment_cases


class BaselinePlanner(Protocol):
    async def plan(self, *, city_stay, requirement, candidates) -> CityPlan:
        """Generate one city plan from the frozen candidate pool."""


@dataclass(frozen=True)
class BaselineResult:
    case_id: str
    case_title: str
    baseline_id: str
    baseline_label: str
    source: str
    metrics: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "case_title": self.case_title,
            "baseline_id": self.baseline_id,
            "baseline_label": self.baseline_label,
            "source": self.source,
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class LLMBaselineAdapter:
    """Metadata plus an injected city planner used for baseline evaluation."""

    planner: BaselinePlanner
    source: str = "llm"
    baseline_id: str = "llm_direct"
    baseline_label: str = "LLM 直接生成"

    async def run(self, case: ExperimentCase) -> BaselineResult:
        return await run_baseline(
            case,
            self.planner,
            source=self.source,
            baseline_id=self.baseline_id,
            baseline_label=self.baseline_label,
        )


async def run_baseline(
    case: ExperimentCase,
    planner: BaselinePlanner,
    *,
    source: str = "llm",
    baseline_id: str = "llm_direct",
    baseline_label: str = "LLM 直接生成",
) -> BaselineResult:
    """Run and score one direct-plan baseline for a fixed experiment case."""

    started = time.perf_counter()
    schedule_builder = ScheduleBuilder(
        day_start_time=case.requirement.daily_start_time or "08:00",
        meal_minutes=40,
        buffer_minutes=20,
    )
    selected_by_day: list[list[str]] = []
    selected_candidates: list[PlanningCandidate] = []
    errors: list[str] = []
    unknown_count = 0
    duplicate_count = 0
    structure_error_count = 0
    route_unavailable_count = 0
    time_window_violations = 0
    route_segments: list[RouteSegment] = []
    for city_case in case.cities:
        try:
            plan = await planner.plan(
                city_stay=city_case.city_stay,
                requirement=case.requirement,
                candidates=list(city_case.candidates),
            )
        except Exception as exc:  # noqa: BLE001 - baseline failures are data
            errors.append(f"{city_case.city_stay.city}: {type(exc).__name__}: {exc}")
            structure_error_count += 1
            selected_by_day.extend([[] for _ in range(city_case.city_stay.days)])
            continue

        if plan.city != city_case.city_stay.city:
            structure_error_count += 1
            errors.append(f"{city_case.city_stay.city}: 返回城市不匹配")
        if len(plan.days) != city_case.city_stay.days:
            structure_error_count += 1
            errors.append(
                f"{city_case.city_stay.city}: 返回 {len(plan.days)} 天，期望 {city_case.city_stay.days} 天"
            )

        city_metrics = _score_city_plan(
            city_case,
            plan,
            requirement=case.requirement,
            schedule_builder=schedule_builder,
        )
        selected_by_day.extend(city_metrics["selected_by_day"])
        selected_candidates.extend(city_metrics["selected_candidates"])
        unknown_count += city_metrics["unknown_count"]
        duplicate_count += city_metrics["duplicate_count"]
        route_unavailable_count += city_metrics["route_unavailable_count"]
        time_window_violations += city_metrics["time_window_violations"]
        route_segments.extend(city_metrics["route_segments"])

    covered_interests = sum(
        any(interest in candidate.interest_tags for candidate in selected_candidates)
        for interest in case.requirement.interests
    )
    route_durations = [
        route.duration_min for route in route_segments if route.duration_min is not None
    ]
    estimated_count = sum(
        route.estimated or route.source == "haversine" for route in route_segments
    )
    selected_count = sum(len(day) for day in selected_by_day)
    metrics: dict[str, object] = {
        "preference_coverage": (
            covered_interests / len(case.requirement.interests)
            if case.requirement.interests
            else 1.0
        ),
        "selected_count": selected_count,
        "valid_attraction_count": len(selected_candidates),
        "unknown_attraction_count": unknown_count,
        "duplicate_attraction_count": duplicate_count,
        "route_minutes": sum(route_durations),
        "route_segments": len(route_segments),
        "average_route_minutes": (
            sum(route_durations) / len(route_durations) if route_durations else 0.0
        ),
        "max_route_minutes": max(route_durations, default=0),
        "estimated_route_ratio": (
            estimated_count / len(route_segments) if route_segments else 0.0
        ),
        "route_unavailable_count": route_unavailable_count,
        "time_window_violations": time_window_violations,
        "structure_error_count": structure_error_count,
        "selected_by_day": selected_by_day,
        "selection_signature": [name for day in selected_by_day for name in day],
        "errors": errors,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "feasible": not (
            errors
            or unknown_count
            or duplicate_count
            or route_unavailable_count
            or time_window_violations
            or structure_error_count
        ),
    }
    return BaselineResult(
        case_id=case.case_id,
        case_title=case.title,
        baseline_id=baseline_id,
        baseline_label=baseline_label,
        source=source,
        metrics=metrics,
    )


async def run_baseline_matrix(
    cases: Iterable[ExperimentCase] | None = None,
    adapter: LLMBaselineAdapter | None = None,
) -> list[BaselineResult]:
    """Run a baseline over all fixed cases using an explicitly supplied adapter."""

    if adapter is None:
        adapter = build_live_llm_baseline()
    if adapter is None:
        raise RuntimeError("未配置 LLM_API_KEY，不能运行真实 LLM Baseline")
    return [await adapter.run(case) for case in (cases or make_experiment_cases())]


def build_live_llm_baseline(settings: Settings | None = None) -> LLMBaselineAdapter | None:
    """Build the opt-in DeepSeek adapter without making a network request."""

    configured = settings or get_settings()
    if not configured.llm_api_key:
        return None
    planner = DeepSeekCityPlanner(
        configured.llm_api_key,
        base_url=configured.llm_base_url,
        model=configured.llm_model,
        timeout_seconds=configured.llm_timeout_seconds,
    )
    return LLMBaselineAdapter(planner=planner)


def write_baseline_results(
    results: Iterable[BaselineResult], output_dir: str | Path
) -> dict[str, Path]:
    """Write baseline metrics in the same JSON/CSV/Markdown family as variants."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    items = [result.to_dict() for result in results]
    json_path = directory / "llm_baseline.json"
    csv_path = directory / "llm_baseline.csv"
    markdown_path = directory / "llm_baseline.md"
    import json

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "case_id",
        "case_title",
        "baseline_id",
        "source",
        "preference_coverage",
        "selected_count",
        "valid_attraction_count",
        "unknown_attraction_count",
        "duplicate_attraction_count",
        "route_minutes",
        "route_segments",
        "estimated_route_ratio",
        "route_unavailable_count",
        "time_window_violations",
        "feasible",
        "elapsed_ms",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            metrics = item["metrics"]
            writer.writerow({field: item.get(field, metrics.get(field)) for field in fields})

    lines = [
        "# LLM 直接生成 Baseline 结果",
        "",
        "结果使用固定候选和路线矩阵评分；只有显式开启实验命令时才会调用 LLM。",
        "",
        "| 案例 | 来源 | 兴趣覆盖率 | 有效景点 | 未知景点 | 交通分钟 | 可行 |",
        "| --- | --- | ---: | ---: | ---: | ---: | :---: |",
    ]
    for item in items:
        metrics = item["metrics"]
        lines.append(
            "| {case} | {source} | {coverage:.1%} | {valid} | {unknown} | {route} | {feasible} |".format(
                case=item["case_id"],
                source=item["source"],
                coverage=float(metrics["preference_coverage"]),
                valid=metrics["valid_attraction_count"],
                unknown=metrics["unknown_attraction_count"],
                route=metrics["route_minutes"],
                feasible="是" if metrics["feasible"] else "否",
            )
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "markdown": markdown_path}


def _score_city_plan(
    city_case: ExperimentCityCase,
    plan: CityPlan,
    *,
    requirement,
    schedule_builder: ScheduleBuilder,
) -> dict[str, object]:
    candidate_by_name = {
        normalize_attraction_name(candidate.name): candidate
        for candidate in city_case.candidates
    }
    selected_by_day: list[list[str]] = []
    selected_candidates: list[PlanningCandidate] = []
    route_segments: list[RouteSegment] = []
    unknown_count = 0
    duplicate_count = 0
    route_unavailable_count = 0
    time_window_violations = 0
    used_names: set[str] = set()

    for day in plan.days:
        names = list(day.attractions)
        selected_by_day.append(names)
        stops: list[AttractionStop] = []
        day_has_unknown = False
        for name in names:
            normalized = normalize_attraction_name(name)
            if normalized in used_names:
                duplicate_count += 1
            if normalized:
                used_names.add(normalized)
            candidate = candidate_by_name.get(normalized)
            if candidate is None:
                unknown_count += 1
                day_has_unknown = True
                continue
            selected_candidates.append(candidate)
            stops.append(_candidate_stop(candidate))

        day_routes: list[RouteSegment] = []
        for origin, destination in zip(stops, stops[1:]):
            key = (origin.poi_id, destination.poi_id)
            duration = city_case.route_minutes.get(key)
            estimated = key in city_case.estimated_pairs and duration is not None
            source = "haversine" if estimated else "amap_mcp"
            if duration is None:
                route_unavailable_count += 1
                source = "unavailable"
            route = RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                origin_longitude=origin.longitude,
                origin_latitude=origin.latitude,
                destination_longitude=destination.longitude,
                destination_latitude=destination.latitude,
                distance_m=(duration or 0) * 100,
                duration_min=duration,
                mode="transit",
                source=source,
                estimated=estimated,
            )
            day_routes.append(route)
            route_segments.append(route)

        if day_has_unknown or not stops:
            time_window_violations += 1
            continue
        trial_day = TravelDay(
            day=day.day,
            city=city_case.city_stay.city,
            city_adcode="experiment",
            city_day=day.day,
            attractions=stops,
            routes=day_routes,
            description=day.description,
        )
        if schedule_builder.try_build_day_exact(
            trial_day,
            day_start_time=requirement.daily_start_time,
            day_end_time=requirement.daily_end_time,
        ) is None:
            time_window_violations += 1

    return {
        "selected_by_day": selected_by_day,
        "selected_candidates": selected_candidates,
        "route_segments": route_segments,
        "unknown_count": unknown_count,
        "duplicate_count": duplicate_count,
        "route_unavailable_count": route_unavailable_count,
        "time_window_violations": time_window_violations,
    }


def _candidate_stop(candidate: PlanningCandidate) -> AttractionStop:
    return AttractionStop(
        poi_id=candidate.poi_id,
        name=candidate.name,
        city=candidate.city,
        category=candidate.category,
        description=candidate.description,
        longitude=candidate.longitude,
        latitude=candidate.latitude,
        visit_duration_minutes=candidate.visit_duration_minutes,
    )


__all__ = [
    "BaselinePlanner",
    "BaselineResult",
    "LLMBaselineAdapter",
    "build_live_llm_baseline",
    "run_baseline",
    "run_baseline_matrix",
    "write_baseline_results",
]
