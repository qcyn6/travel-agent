import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.experiments import (  # noqa: E402
    DeepSeekBaselinePlanner,
    EXPERIMENT_CASES,
    evaluate_baseline_plan,
    evaluate_system_plan,
)
from backend.models.experiment import ExperimentCaseResult  # noqa: E402
from backend.models.travel_plan import TravelPlanResponse  # noqa: E402


class KnownAttractionVerifier:
    def __init__(self, session):
        self.session = session

    def exists(self, city: str, name: str) -> bool:
        return self.session.scalar(
            select(AttractionRow.id)
            .join(CityRow, CityRow.id == AttractionRow.city_id)
            .where(CityRow.name == _city_name(city), AttractionRow.name == name)
            .limit(1)
        ) is not None


def _city_name(city: str) -> str:
    stripped = city.strip()
    return stripped if stripped.endswith("市") else f"{stripped}市"


async def run(args) -> list[ExperimentCaseResult]:
    settings = get_settings()
    baseline_planner = DeepSeekBaselinePlanner(
        settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    start_date = args.start_date
    selected = EXPERIMENT_CASES[: args.limit] if args.limit else EXPERIMENT_CASES
    results = []
    with open_session() as session:
        verifier = KnownAttractionVerifier(session)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
            for case in selected:
                dated_case = case.model_copy(update={"text": f"{start_date}出发，{case.text}"})
                baseline = await baseline_planner.plan(dated_case)
                response = await client.post(
                    "/api/v1/travel/plan",
                    json={"text": dated_case.text, "travel_scope": dated_case.travel_scope},
                )
                response.raise_for_status()
                system = TravelPlanResponse.model_validate(response.json())
                result = ExperimentCaseResult(
                    case_id=case.id,
                    case=dated_case,
                    baseline_plan=baseline,
                    system_response=system,
                    baseline=evaluate_baseline_plan(
                        baseline, dated_case, attraction_exists=verifier.exists
                    ),
                    system=evaluate_system_plan(
                        system, dated_case, attraction_exists=verifier.exists
                    ),
                )
                results.append(result)
                print(
                    f"{case.id} 完成 | 景点有效率 A={result.baseline.attraction_validity:.2f} "
                    f"B={result.system.attraction_validity:.2f}"
                )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 LLM 直接生成与完整系统的 A/B 实验")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--start-date")
    parser.add_argument("--limit", type=int, default=0, help="0 表示运行全部 24 组")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.start_date = args.start_date or (date.today() + timedelta(days=2)).isoformat()
    results = asyncio.run(run(args))
    payload = {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "start_date": args.start_date,
            "case_count": len(results),
            "baseline": "DeepSeek 无工具直接生成",
            "system": "完整旅游规划系统",
        },
        "summary": {
            "baseline": _summarize(results, "baseline"),
            "system": _summarize(results, "system"),
        },
        "results": [result.model_dump(mode="json") for result in results],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已保存：{args.output}")


def _summarize(results: list[ExperimentCaseResult], arm: str) -> dict[str, float | None]:
    fields = (
        "attraction_validity",
        "interest_coverage",
        "schedule_feasibility",
        "route_reasonableness",
        "external_constraint_satisfaction",
    )
    summary = {}
    for field in fields:
        values = [getattr(getattr(result, arm), field) for result in results]
        available = [value for value in values if value is not None]
        summary[field] = sum(available) / len(available) if available else None
    return summary


if __name__ == "__main__":
    main()
