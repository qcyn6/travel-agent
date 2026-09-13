import argparse
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models.travel_plan import TravelPlanResponse


CASES = [
    {
        "name": "杭州 1 天",
        "request": {
            "text": "2026年9月20日去杭州玩一天，喜欢历史文化和拍照，希望不要太赶",
            "travel_scope": "single_city",
        },
        "days": 1,
        "cities": ["杭州市"],
    },
    {
        "name": "杭州 3 天",
        "request": {
            "text": "2026年9月20日去杭州玩三天，喜欢自然和人文，行程轻松",
            "travel_scope": "single_city",
        },
        "days": 3,
        "cities": ["杭州市"],
    },
    {
        "name": "云南 7 天",
        "request": {
            "text": "2026年9月20日去云南玩七天，想去两三个城市，喜欢自然、古城和摄影",
            "travel_scope": "multi_city",
        },
        "days": 7,
        "cities": ["昆明市", "大理市", "丽江市"],
    },
]


def validate_case(body: dict, case: dict) -> TravelPlanResponse:
    result = TravelPlanResponse.model_validate(body)
    plan = result.plan
    assert result.requirement.days == case["days"]
    assert len(plan.itinerary) == case["days"]
    assert [day.day for day in plan.itinerary] == list(range(1, case["days"] + 1))
    assert [stay.city for stay in plan.city_stays] == case["cities"]
    assert plan.evaluation is not None
    assert plan.replan_count <= 2
    for day in plan.itinerary:
        assert day.schedule is not None
        assert day.weather is not None
        assert len(day.routes) == max(0, len(day.attractions) - 1)
        for stop in day.attractions:
            assert stop.poi_id
            assert stop.start_time and stop.end_time
            assert stop.estimated_visit_minutes
            assert stop.opening_hours is not None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="运行最终版本三条真实工作流验收用例")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        health = client.get("/health")
        health.raise_for_status()
        print(f"服务里程碑：{health.json().get('milestone')}")
        for case in CASES:
            started = time.perf_counter()
            response = client.post("/api/v1/travel/plan", json=case["request"])
            response.raise_for_status()
            result = validate_case(response.json(), case)
            elapsed = time.perf_counter() - started
            print(
                f"通过：{case['name']} | {elapsed:.1f}s | "
                f"{len(result.plan.itinerary)} 天 | "
                f"重规划 {result.plan.replan_count} 次 | "
                f"最终检查={'通过' if result.plan.evaluation.passed else '待确认'}"
            )
            if not result.plan.evaluation.passed:
                remaining = "；".join(
                    f"Day {issue.day or '-'} {issue.type}/{issue.severity}"
                    for issue in result.plan.evaluation.issues
                )
                print(f"  剩余提示：{remaining}")


if __name__ == "__main__":
    main()
