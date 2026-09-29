from backend.models.evaluation import DayEvaluationMetric, EvaluationIssue, EvaluationResult
from backend.models.travel_plan import TravelDay, TravelPlan
from backend.schedule.opening_hours import (
    opening_intervals_for_date,
    visit_fits_opening_hours,
)


class PlanEvaluator:
    def __init__(
        self,
        *,
        max_route_segment_minutes: int = 60,
        max_daily_route_minutes: int = 180,
    ):
        self.max_route_segment_minutes = max(0, int(max_route_segment_minutes))
        self.max_daily_route_minutes = max(0, int(max_daily_route_minutes))

    def evaluate(self, plan: TravelPlan, requirement) -> EvaluationResult:
        """Review route reliability and schedule constraints without replanning."""

        issues: list[EvaluationIssue] = []
        for day in plan.itinerary:
            issues.extend(self._evaluate_routes(day))
            issues.extend(self._evaluate_schedule(day, requirement))
            issues.extend(self._evaluate_external_status(day))
            for warning in _manual_warnings(plan, day.day):
                if any(issue.day == day.day and issue.message == warning for issue in issues):
                    continue
                issues.append(
                    EvaluationIssue(
                        type="manual_adjustment",
                        severity="medium",
                        day=day.day,
                        message=warning,
                        suggestion="这是你手动选择后的提示；请按实际情况决定是否保留。",
                    )
                )
        issues = self._deduplicate_issues(issues)
        hard_issue_exists = any(issue.severity == "high" for issue in issues)
        return EvaluationResult(
            passed=not hard_issue_exists,
            issues=issues,
            daily_metrics=self._daily_metrics(plan),
        )

    def _evaluate_routes(self, day: TravelDay) -> list[EvaluationIssue]:
        issues: list[EvaluationIssue] = []
        routes = _scheduled_routes(day)
        route_minutes = sum(route.duration_min or 0 for route in routes)
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
        for route in routes:
            if (
                route.duration_min is not None
                and route.duration_min > self.max_route_segment_minutes
            ):
                issues.append(
                    EvaluationIssue(
                        type="route",
                        severity="medium",
                        day=day.day,
                        message=(
                            f"{route.origin_name}到{route.destination_name}预计需要"
                            f" {route.duration_min} 分钟"
                        ),
                        suggestion="可考虑手动更换景点；系统不会自动改动你的行程",
                    )
                )
        return issues

    @staticmethod
    def _evaluate_schedule(day: TravelDay, requirement) -> list[EvaluationIssue]:
        issues: list[EvaluationIssue] = []
        window_start = _clock_minutes(getattr(requirement, "daily_start_time", None))
        window_end = _clock_minutes(getattr(requirement, "daily_end_time", None))

        if window_end is not None:
            end_time = (
                _clock_minutes(day.schedule.end_time)
                if day.schedule is not None
                else None
            )
            outside_stops = [
                stop for stop in day.attractions
                if (stop_end := _clock_minutes(stop.end_time)) is not None
                and stop_end > window_end
            ]
            if end_time is not None and end_time > window_end:
                # Keep the message identical to manual replacement previews so
                # the evaluator can de-duplicate the same warning cleanly.
                issues.append(
                    EvaluationIssue(
                        type="schedule",
                        severity="medium",
                        day=day.day,
                        message=(
                            f"第{day.day}天预计到{day.schedule.end_time}结束，"
                            f"超过你设置的{getattr(requirement, 'daily_end_time')}。"
                        ),
                        suggestion="行程仍保留，请确认是否接受超出时间窗口。",
                    )
                )
            elif outside_stops:
                names = "、".join(stop.name for stop in outside_stops)
                issues.append(
                    EvaluationIssue(
                        type="schedule",
                        severity="medium",
                        day=day.day,
                        message=f"第 {day.day} 天{names}的游览结束时间超出每日时间窗口",
                        suggestion="请核对景点游览时长或手动调整行程。",
                    )
                )

        if window_start is not None:
            early_stops = [
                stop for stop in day.attractions
                if (stop_start := _clock_minutes(stop.start_time)) is not None
                and stop_start < window_start
            ]
            if early_stops:
                names = "、".join(stop.name for stop in early_stops)
                issues.append(
                    EvaluationIssue(
                        type="schedule",
                        severity="medium",
                        day=day.day,
                        message=f"第 {day.day} 天{names}的游览开始时间早于每日时间窗口",
                        suggestion="请核对酒店到首站的交通时间和当天开始时间。",
                    )
                )

        for stop in day.attractions:
            if (
                not stop.opening_hours_weekly
                or not stop.start_time
                or opening_intervals_for_date(stop.opening_hours_weekly, day.date) is None
            ):
                continue
            start = _clock_minutes(stop.start_time)
            end = _clock_minutes(stop.end_time)
            duration = stop.visit_duration_minutes
            if start is None:
                continue
            if end is not None:
                duration = max(0, end - start)
            if not visit_fits_opening_hours(
                stop.opening_hours_weekly,
                day.date,
                start,
                duration,
            ):
                issues.append(
                    EvaluationIssue(
                        type="schedule",
                        severity="medium",
                        day=day.day,
                        message=(
                            f"{stop.name}预计{stop.start_time or '到达时'}游览，"
                            "可能不在已知营业时间内。"
                        ),
                        suggestion="高德营业时间仅供参考，请核实后决定是否保留。",
                    )
                )
        return issues

    @staticmethod
    def _evaluate_external_status(day: TravelDay) -> list[EvaluationIssue]:
        """Expose route degradation as user-visible notices."""

        issues: list[EvaluationIssue] = []
        routes = _scheduled_routes(day)
        estimated_routes = sum(
            route.estimated or route.source == "haversine" for route in routes
        )
        unavailable_routes = sum(route.source == "unavailable" for route in routes)
        if estimated_routes:
            issues.append(
                EvaluationIssue(
                    type="route",
                    severity="medium",
                    day=day.day,
                    message=f"第 {day.day} 天有 {estimated_routes} 段路线不是地图真实路线",
                    suggestion="出发前用地图再次确认交通时间",
                )
            )
        if unavailable_routes:
            issues.append(
                EvaluationIssue(
                    type="route",
                    severity="high",
                    day=day.day,
                    message=f"第 {day.day} 天有 {unavailable_routes} 段路线无法获取",
                    suggestion="出发前确认这段交通方式和耗时",
                )
            )
        if day.weather is not None and day.weather.status != "available":
            label = "未查询" if day.weather.status == "skipped" else "暂不可用"
            issues.append(
                EvaluationIssue(
                    type="weather",
                    severity="low",
                    day=day.day,
                    message=f"第 {day.day} 天天气预报{label}",
                    suggestion="出发前再次确认天气",
                )
            )
        return issues

    def _daily_metrics(self, plan: TravelPlan) -> list[DayEvaluationMetric]:
        metrics: list[DayEvaluationMetric] = []
        for day in plan.itinerary:
            scheduled_routes = _scheduled_routes(day)
            displayed_routes = _map_routes(day)
            route_count = len(displayed_routes)
            estimated_count = sum(
                route.estimated or route.source == "haversine"
                for route in displayed_routes
            )
            metrics.append(
                DayEvaluationMetric(
                    day=day.day,
                    attraction_count=len(day.attractions),
                    # The end-of-day hotel leg is visible on the map, but is
                    # intentionally outside the day's schedule and travel total.
                    route_minutes=sum(route.duration_min or 0 for route in scheduled_routes),
                    route_segments=route_count,
                    estimated_route_ratio=(estimated_count / route_count) if route_count else 0.0,
                )
            )
        return metrics

    @staticmethod
    def _deduplicate_issues(issues: list[EvaluationIssue]) -> list[EvaluationIssue]:
        result: list[EvaluationIssue] = []
        seen: set[tuple[str, int | None, str]] = set()
        for issue in issues:
            key = (issue.type, issue.day, issue.message)
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result


def _manual_warnings(plan: TravelPlan, day: int) -> list[str]:
    warnings = plan.manual_adjustment_warnings.get(day)
    if warnings is None:
        warnings = plan.manual_adjustment_warnings.get(str(day), [])
    return [warning.strip() for warning in warnings if isinstance(warning, str) and warning.strip()]


def _clock_minutes(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        day_offset = 1440 if value.startswith("次日") else 0
        clock_value = value.removeprefix("次日")
        hour, minute = clock_value.split(":", 1)
        hour_value, minute_value = int(hour), int(minute)
        if not 0 <= hour_value <= 23 or not 0 <= minute_value <= 59:
            return None
        return day_offset + hour_value * 60 + minute_value
    except (AttributeError, TypeError, ValueError):
        return None


def _scheduled_routes(day: TravelDay):
    return ([day.start_hotel_route] if day.start_hotel_route is not None else []) + day.routes


def _map_routes(day: TravelDay):
    """Return every route segment displayed on the day's map."""

    return _scheduled_routes(day) + ([day.hotel_route] if day.hotel_route is not None else [])
