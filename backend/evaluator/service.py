from datetime import date

from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.travel_plan import TravelDay, TravelPlan


class PlanEvaluator:
    def __init__(
        self,
        *,
        max_day_minutes: int = 600,
        max_route_segment_minutes: int = 60,
        max_daily_route_minutes: int = 180,
        rain_probability_threshold: float = 0.5,
        outdoor_ratio_threshold: float = 0.5,
        minimum_interest_coverage: float = 0.5,
    ):
        self.max_day_minutes = max_day_minutes
        self.max_route_segment_minutes = max_route_segment_minutes
        self.max_daily_route_minutes = max_daily_route_minutes
        self.rain_probability_threshold = rain_probability_threshold
        self.outdoor_ratio_threshold = outdoor_ratio_threshold
        self.minimum_interest_coverage = minimum_interest_coverage

    def evaluate(self, plan: TravelPlan, requirement) -> EvaluationResult:
        issues: list[EvaluationIssue] = []
        for day in plan.itinerary:
            issues.extend(self._evaluate_schedule(day))
            issues.extend(self._evaluate_routes(day))
            issues.extend(self._evaluate_weather(day))
            issues.extend(self._evaluate_opening_hours(day))
        issues.extend(self._evaluate_preferences(plan, requirement.interests))
        hard_issue_exists = any(issue.severity == "high" for issue in issues)
        return EvaluationResult(
            passed=not hard_issue_exists,
            issues=issues,
            replan_required=hard_issue_exists,
        )

    def _evaluate_schedule(self, day: TravelDay) -> list[EvaluationIssue]:
        if day.schedule is None or day.schedule.total_minutes <= self.max_day_minutes:
            return []
        hours = round(day.schedule.total_minutes / 60, 1)
        return [
            EvaluationIssue(
                type="schedule_density",
                severity="high",
                day=day.day,
                message=f"第 {day.day} 天预计总耗时约 {hours} 小时，行程过密",
                suggestion="减少一个景点或缩短部分景点游览时间",
            )
        ]

    def _evaluate_routes(self, day: TravelDay) -> list[EvaluationIssue]:
        issues = []
        route_minutes = sum(route.duration_min or 0 for route in day.routes)
        if route_minutes > self.max_daily_route_minutes:
            issues.append(
                EvaluationIssue(
                    type="route",
                    severity="high",
                    day=day.day,
                    message=f"第 {day.day} 天交通总时长约 {route_minutes} 分钟，移动成本过高",
                    suggestion="减少远距离景点或调整景点组合",
                )
            )
        for route in day.routes:
            if route.duration_min is not None and route.duration_min > self.max_route_segment_minutes:
                issues.append(
                    EvaluationIssue(
                        type="route",
                        severity="medium",
                        day=day.day,
                        message=(
                            f"{route.origin_name}到{route.destination_name}预计需要"
                            f" {route.duration_min} 分钟"
                        ),
                        suggestion="优先替换为距离更近的景点或调整顺序",
                    )
                )
        return issues

    def _evaluate_weather(self, day: TravelDay) -> list[EvaluationIssue]:
        weather = day.weather
        if weather is None or weather.status != "available":
            return []
        rainy = (
            weather.precipitation_probability is not None
            and weather.precipitation_probability >= self.rain_probability_threshold
        ) or any(word in (weather.condition or "") for word in ("雨", "雪", "雷"))
        if not rainy:
            return []
        outdoor_weight = sum(
            1 if stop.environment == "outdoor" else 0.5 if stop.environment == "mixed" else 0
            for stop in day.attractions
        )
        if outdoor_weight / len(day.attractions) <= self.outdoor_ratio_threshold:
            return []
        severity = (
            "high"
            if weather.precipitation_probability is not None
            and weather.precipitation_probability >= 0.7
            else "medium"
        )
        return [
            EvaluationIssue(
                type="weather",
                severity=severity,
                day=day.day,
                message=f"第 {day.day} 天可能有{weather.condition or '降水'}，但主要安排为户外景点",
                suggestion="增加室内景点或调整游览日期",
            )
        ]

    def _evaluate_opening_hours(self, day: TravelDay) -> list[EvaluationIssue]:
        issues = []
        for stop in day.attractions:
            opening = stop.opening_hours
            arrival = _clock_minutes(stop.start_time)
            if opening is None or opening.status != "known" or arrival is None:
                continue
            periods = _applicable_periods(opening.periods, day.date)
            if not periods:
                issues.append(
                    EvaluationIssue(
                        type="opening_hours",
                        severity="high",
                        day=day.day,
                        message=f"{stop.name}在计划日期没有可用开放时段",
                        suggestion="调整游览日期或替换景点",
                    )
                )
                continue
            latest_entry = max(
                _clock_minutes(period.last_entry_time or period.close_time) or 0
                for period in periods
            )
            earliest_open = min(_clock_minutes(period.open_time) or 0 for period in periods)
            if arrival > latest_entry:
                issues.append(
                    EvaluationIssue(
                        type="opening_hours",
                        severity="high",
                        day=day.day,
                        message=f"预计到达{stop.name}的时间晚于最后入园时间",
                        suggestion="调整景点顺序或替换景点",
                    )
                )
            elif arrival < earliest_open:
                issues.append(
                    EvaluationIssue(
                        type="opening_hours",
                        severity="medium",
                        day=day.day,
                        message=f"预计到达{stop.name}时景点尚未开放",
                        suggestion="推迟到达时间或调整景点顺序",
                    )
                )
        return issues

    def _evaluate_preferences(self, plan: TravelPlan, interests: list[str]) -> list[EvaluationIssue]:
        if not interests:
            return []
        searchable = " ".join(
            f"{stop.name} {stop.category or ''} {stop.description or ''}"
            for day in plan.itinerary
            for stop in day.attractions
        ).lower()
        covered = sum(_interest_matches(interest, searchable) for interest in interests)
        coverage = covered / len(interests)
        if coverage >= self.minimum_interest_coverage:
            return []
        return [
            EvaluationIssue(
                type="preference",
                severity="medium",
                message=f"当前行程对明确兴趣的覆盖率约为 {round(coverage * 100)}%",
                suggestion="优先补充与用户兴趣更匹配的景点",
            )
        ]


def _clock_minutes(value: str | None) -> int | None:
    if not value or "日" in value:
        return None
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError:
        return None
    return hour * 60 + minute


def _applicable_periods(periods, target_date: date | None):
    if target_date is None:
        return periods
    weekday = target_date.isoweekday()
    return [period for period in periods if not period.weekdays or weekday in period.weekdays]


_INTEREST_ALIASES = {
    "摄影": ("摄影", "拍照", "风景", "古城", "古镇"),
    "拍照": ("摄影", "拍照", "风景", "古城", "古镇"),
    "历史": ("历史", "文化", "遗址", "古城", "古镇", "博物馆"),
    "文化": ("文化", "人文", "历史", "博物馆", "古城", "古镇"),
    "自然": ("自然", "山", "湖", "湿地", "森林", "公园", "风景"),
    "美食": ("美食", "餐饮", "小吃"),
}


def _interest_matches(interest: str, searchable: str) -> bool:
    normalized = interest.strip().lower()
    aliases = next((values for key, values in _INTEREST_ALIASES.items() if key in normalized), (normalized,))
    return any(alias in searchable for alias in aliases)
