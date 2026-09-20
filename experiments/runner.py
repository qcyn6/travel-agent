from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop
from backend.planner.dynamic_scheduler import DynamicCityScheduler
from backend.schedule import ScheduleBuilder

from experiments.cases import ExperimentCase, ExperimentCityCase, make_experiment_cases
from experiments.variants import EXPERIMENT_VARIANTS, ExperimentVariant


@dataclass(frozen=True)
class ExperimentResult:
    case_id: str
    case_title: str
    variant_id: str
    variant_label: str
    metrics: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "case_title": self.case_title,
            "variant_id": self.variant_id,
            "variant_label": self.variant_label,
            "metrics": self.metrics,
        }


class FixedRouteService:
    """Deterministic route adapter backed by one frozen experiment case."""

    def __init__(self, city_case: ExperimentCityCase):
        self.city_case = city_case

    async def resolve_segment(
        self,
        origin: AttractionStop,
        destination: AttractionStop,
        *,
        requested_mode: str,
        city_adcode: str,
    ) -> RouteSegment:
        del requested_mode, city_adcode
        key = (origin.poi_id, destination.poi_id)
        duration = self.city_case.route_minutes.get(key)
        estimated = key in self.city_case.estimated_pairs
        source = "haversine" if estimated else "amap_mcp"
        return RouteSegment(
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
            source=source if duration is not None else "unavailable",
            estimated=estimated and duration is not None,
        )


async def run_variant(case: ExperimentCase, variant: ExperimentVariant) -> ExperimentResult:
    started = time.perf_counter()
    requirement = case.requirement
    scheduler = DynamicCityScheduler(
        meal_minutes=40,
        buffer_minutes=20,
        day_start_time=requirement.daily_start_time or "08:00",
        day_end_time=requirement.daily_end_time or "21:00",
        preference_weight=variant.preference_weight,
        distance_weight=variant.distance_weight,
    )
    schedule_builder = ScheduleBuilder(
        day_start_time=requirement.daily_start_time or "08:00",
        meal_minutes=40,
        buffer_minutes=20,
    )
    selected_candidates = []
    all_routes: list[RouteSegment] = []
    decisions = []
    selected_by_day: list[list[str]] = []

    for city_case in case.cities:
        result = await scheduler.plan_with_routes(
            city_stay=city_case.city_stay,
            requirement=requirement,
            candidates=city_case.candidates,
            city_adcode="experiment",
            route_service=FixedRouteService(city_case),
            schedule_builder=schedule_builder,
        )
        candidate_by_id = {candidate.poi_id: candidate for candidate in city_case.candidates}
        for day in result.city_plan.days:
            selected_by_day.append(list(day.attractions))
            selected_candidates.extend(
                candidate
                for candidate in candidate_by_id.values()
                if candidate.name in day.attractions
            )
        all_routes.extend(route for routes in result.routes_by_day for route in routes)
        decisions.extend(result.decisions)

    covered_interests = sum(
        any(interest in candidate.interest_tags for candidate in selected_candidates)
        for interest in requirement.interests
    )
    route_durations = [route.duration_min for route in all_routes if route.duration_min is not None]
    estimated_count = sum(route.estimated or route.source == "haversine" for route in all_routes)
    rejected = [decision for decision in decisions if not decision.selected]
    metrics = {
        "preference_coverage": (covered_interests / len(requirement.interests))
        if requirement.interests
        else 1.0,
        "attraction_count": len(selected_candidates),
        "route_minutes": sum(route_durations),
        "route_segments": len(all_routes),
        "average_route_minutes": (sum(route_durations) / len(route_durations))
        if route_durations
        else 0.0,
        "max_route_minutes": max(route_durations, default=0),
        "estimated_route_ratio": estimated_count / len(all_routes) if all_routes else 0.0,
        "decision_count": len(decisions),
        "rejected_candidate_count": len(rejected),
        "route_unavailable_rejections": sum(
            decision.rejected_reason == "route_unavailable" for decision in rejected
        ),
        "time_window_rejections": sum(
            decision.rejected_reason == "time_window" for decision in rejected
        ),
        "selected_by_day": selected_by_day,
        "selection_signature": [name for day in selected_by_day for name in day],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "feasible": True,
    }
    return ExperimentResult(
        case_id=case.case_id,
        case_title=case.title,
        variant_id=variant.variant_id,
        variant_label=variant.label,
        metrics=metrics,
    )


async def run_matrix(
    cases: Iterable[ExperimentCase] | None = None,
    variants: Iterable[ExperimentVariant] | None = None,
) -> list[ExperimentResult]:
    selected_cases = tuple(cases or make_experiment_cases())
    selected_variants = tuple(variants or EXPERIMENT_VARIANTS)
    results: list[ExperimentResult] = []
    for case in selected_cases:
        for variant in selected_variants:
            results.append(await run_variant(case, variant))
    return results


def write_results(results: Iterable[ExperimentResult], output_dir: str | Path) -> dict[str, Path]:
    """Write JSON, CSV and a short Markdown summary for paper preparation."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    items = [result.to_dict() for result in results]
    json_path = directory / "results.json"
    csv_path = directory / "results.csv"
    markdown_path = directory / "summary.md"
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "case_id",
        "case_title",
        "variant_id",
        "variant_label",
        "preference_coverage",
        "attraction_count",
        "route_minutes",
        "route_segments",
        "average_route_minutes",
        "max_route_minutes",
        "estimated_route_ratio",
        "decision_count",
        "rejected_candidate_count",
        "route_unavailable_rejections",
        "time_window_rejections",
        "feasible",
        "elapsed_ms",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            metrics = item["metrics"]
            writer.writerow(
                {
                    field: item.get(field, metrics.get(field))
                    for field in fields
                }
            )

    lines = [
        "# 规划算法离线实验结果",
        "",
        "固定候选景点与路线矩阵由 `experiments/cases.py` 提供；实验不访问数据库或外部 API。",
        "",
        "| 案例 | 变体 | 兴趣覆盖率 | 景点数 | 交通分钟 | 估算路线比例 | 被拒候选 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in items:
        metrics = item["metrics"]
        lines.append(
            "| {case} | {variant} | {coverage:.1%} | {attractions} | {route} | {estimated:.1%} | {rejected} |".format(
                case=item["case_id"],
                variant=item["variant_id"],
                coverage=float(metrics["preference_coverage"]),
                attractions=metrics["attraction_count"],
                route=metrics["route_minutes"],
                estimated=float(metrics["estimated_route_ratio"]),
                rejected=metrics["rejected_candidate_count"],
            )
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "markdown": markdown_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="运行可重复的规划算法离线实验")
    parser.add_argument(
        "--output",
        default="experiments/outputs",
        help="实验结果输出目录",
    )
    args = parser.parse_args()
    results = asyncio.run(run_matrix())
    paths = write_results(results, args.output)
    print(f"完成 {len(results)} 条实验记录")
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()


__all__ = ["ExperimentResult", "FixedRouteService", "run_matrix", "run_variant", "write_results"]
