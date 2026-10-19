"""Pure, presentation-only application of AI plan review results.

The planner owns POI selection, routes, hotels and schedules. This module
only applies descriptions, weather advice and advisory evaluation issues.
"""

import re

from backend.degradation import collect_degradation_notices
from backend.models.evaluation import EvaluationIssue
from backend.models.travel_plan import TravelPlan, TravelPlanResponse
from backend.models.weather import DailyWeatherAdvice
from backend.plan_overview import synchronize_plan_overviews
from backend.weather.advice import fallback_travel_advice


def apply_plan_review(response: TravelPlanResponse, review: dict) -> tuple[TravelPlanResponse, str]:
    """Return a new response and progress label, without mutating inputs."""
    plan = response.plan
    evaluation = plan.evaluation
    if evaluation is not None:
        review_issues = list(evaluation.issues)
        seen = {(issue.type, issue.day, issue.message) for issue in review_issues}
        valid_days = {day.day for day in plan.itinerary}
        for item in review.get("issues", []):
            if isinstance(item, dict):
                message = str(item.get("message", "")).strip()
                day = item.get("day")
                day = day if isinstance(day, int) and not isinstance(day, bool) else None
                if day not in valid_days:
                    continue
            else:
                message = str(item).strip() if item is not None else ""
                day_match = re.search(r"第\s*(\d+)\s*天", message)
                day = int(day_match.group(1)) if day_match else None
                if day is not None and day not in valid_days:
                    continue
            if not message:
                continue
            if _is_generic_manual_review_issue(message):
                continue
            category = _review_issue_category(
                item.get("category", "other") if isinstance(item, dict) else "other",
                message,
            )
            if _review_issue_is_already_covered(
                category=category,
                day=day,
                message=message,
                plan=plan,
                deterministic_issues=evaluation.issues,
            ):
                continue
            if not message.startswith("复核提示："):
                message = f"复核提示：{message}"
            key = ("review", day, message)
            if key in seen:
                continue
            seen.add(key)
            review_issues.append(
                EvaluationIssue(
                    type="review",
                    severity="medium",
                    day=day,
                    message=message,
                    suggestion=None,
                )
            )
        if len(review_issues) != len(evaluation.issues):
            plan = plan.model_copy(
                update={
                    "evaluation": evaluation.model_copy(
                        update={"issues": review_issues}
                    )
                }
            )

    advice_by_day = {}
    for item in review.get("weather_advice", []):
        try:
            advice = DailyWeatherAdvice.model_validate(item)
        except (TypeError, ValueError):
            continue
        advice_by_day[advice.day] = advice.advice.strip()

    day_descriptions = {}
    for item in review.get("day_descriptions", []):
        if not isinstance(item, dict):
            continue
        try:
            day_number = int(item.get("day"))
        except (TypeError, ValueError):
            continue
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            day_descriptions[day_number] = description.strip()

    known_poi_ids = {
        stop.poi_id
        for day in plan.itinerary
        for stop in day.attractions
    }
    attraction_descriptions = {}
    for item in review.get("attraction_descriptions", []):
        if not isinstance(item, dict):
            continue
        poi_id = item.get("poi_id")
        description = item.get("description")
        if (
            isinstance(poi_id, str)
            and poi_id in known_poi_ids
            and isinstance(description, str)
            and description.strip()
        ):
            attraction_descriptions[poi_id] = description.strip()

    advised_days = 0
    enhanced_day_count = 0
    enhanced_attraction_count = 0
    itinerary = []
    for day in plan.itinerary:
        attractions = []
        for stop in day.attractions:
            description = attraction_descriptions.get(stop.poi_id)
            if description and description != stop.description:
                stop = stop.model_copy(update={"description": description})
                enhanced_attraction_count += 1
            attractions.append(stop)

        updates = {"attractions": attractions}
        day_description = day_descriptions.get(day.day)
        if day_description and day_description != day.description:
            updates["description"] = day_description
            enhanced_day_count += 1

        weather = day.weather
        if weather is not None and weather.status == "available":
            advice = advice_by_day.get(day.day) or weather.travel_advice
            source = "ai" if day.day in advice_by_day else weather.travel_advice_source
            if not advice:
                advice = fallback_travel_advice(weather)
                source = "rule_based"
            if advice:
                weather = weather.model_copy(
                    update={
                        "travel_advice": advice,
                        "travel_advice_source": source or "ai",
                    }
                )
                advised_days += 1
                updates["weather"] = weather
        day = day.model_copy(update=updates)
        itinerary.append(day)

    city_day_descriptions = {
        (day.city, day.city_day): day.description
        for day in itinerary
        if day.day in day_descriptions
    }
    city_plans = []
    for city_plan in plan.city_plans:
        days = [
            city_day.model_copy(
                update={"description": city_day_descriptions[(city_plan.city, city_day.day)]}
            )
            if (city_plan.city, city_day.day) in city_day_descriptions
            else city_day
            for city_day in city_plan.days
        ]
        city_plans.append(city_plan.model_copy(update={"days": days}))

    if itinerary != plan.itinerary or city_plans != plan.city_plans:
        plan = plan.model_copy(update={"itinerary": itinerary, "city_plans": city_plans})
    response = response.model_copy(update={"plan": plan})
    if review.get("approved", True) or not review.get("repairable", False):
        label = (
            f"已增强 {enhanced_attraction_count} 个景点介绍、{enhanced_day_count} 天概览，"
            f"整理 {advised_days} 天天气建议；核心排程保持不变"
        )
        return response, label
    repaired_plan = synchronize_plan_overviews(response.plan)
    repaired_plan = repaired_plan.model_copy(
        update={"notices": collect_degradation_notices(repaired_plan)}
    )
    changed = repaired_plan != response.plan
    label = "已完成安全修复" if changed else "未发现可安全自动修复的问题"
    if not changed:
        return response, label
    return TravelPlanResponse(
        requirement=response.requirement,
        plan=repaired_plan,
    ), label


def _review_issue_category(category, message: str) -> str:
    allowed = {"route", "schedule", "opening_hours", "other"}
    if category in allowed and category != "other":
        return category
    text = re.sub(r"\s+", "", message)
    if any(token in text for token in ("营业时间", "开放时间", "营业信息")):
        return "opening_hours"
    if any(token in text for token in ("交通", "路线", "路程", "移动", "分钟", "公里")):
        return "route"
    if any(token in text for token in ("时间窗口", "超出时间", "安排过晚", "开始过早")):
        return "schedule"
    return "other"


def _is_generic_manual_review_issue(message: str) -> bool:
    text = re.sub(r"\s+", "", message)
    if any(
        token in text
        for token in (
            "复核提示不会自动修改",
            "确定性评估已提示",
            "请结合行程详情自行判断",
            "按情况决定是否保留",
            "手动调整后的结果",
        )
    ):
        return True
    return "手动调整" in text and any(
        token in text for token in ("是否保留", "按情况", "自行判断", "仅作提醒")
    )


def _review_issue_is_already_covered(
    *,
    category: str,
    day: int | None,
    message: str,
    plan: TravelPlan,
    deterministic_issues: list[EvaluationIssue],
) -> bool:
    day_issues = [issue for issue in deterministic_issues if issue.day == day]
    if category in {"route", "schedule"}:
        return any(issue.type == category for issue in day_issues)
    if category != "opening_hours":
        return False

    day_plan = next((item for item in plan.itinerary if item.day == day), None)
    if day_plan is None:
        return False
    covered_opening_issues = [
        issue
        for issue in day_issues
        if issue.type == "schedule"
        and any(token in issue.message for token in ("营业时间", "开放时间"))
    ]
    if not covered_opening_issues:
        return False
    mentioned_stops = [
        stop.name
        for stop in day_plan.attractions
        if stop.name and stop.name in message
    ]
    if mentioned_stops:
        covered_names = {
            stop.name
            for stop in day_plan.attractions
            if stop.name
            and any(stop.name in issue.message for issue in covered_opening_issues)
        }
        return all(name in covered_names for name in mentioned_stops)
    return any(
        any(token in message for token in ("营业时间", "开放时间"))
        and any(token in issue.message for token in ("营业时间", "开放时间"))
        for issue in covered_opening_issues
    )
