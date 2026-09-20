from collections.abc import Mapping, Sequence

from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.travel_plan import TravelDay, TravelPlan


class PlanEvaluator:
    def __init__(
        self,
        *,
        max_day_minutes: int = 600,
        max_route_segment_minutes: int = 60,
        max_daily_route_minutes: int = 180,
        minimum_interest_coverage: float = 0.5,
    ):
        self.max_day_minutes = max_day_minutes
        self.max_route_segment_minutes = max_route_segment_minutes
        self.max_daily_route_minutes = max_daily_route_minutes
        self.minimum_interest_coverage = minimum_interest_coverage

    def evaluate(self, plan: TravelPlan, requirement) -> EvaluationResult:
        """Evaluate a plan using the public, backwards-compatible contract."""

        return self.evaluate_with_context(plan, requirement)

    def evaluate_with_context(
        self,
        plan: TravelPlan,
        requirement,
        *,
        interest_tags_by_poi: Mapping[str, Sequence[str]] | None = None,
    ) -> EvaluationResult:
        """Evaluate with transient retrieval context when it is available.

        The tag mapping belongs to one planning run only. It is intentionally
        not persisted on ``AttractionStop`` or in the attractions table, while
        allowing the evaluator to use the same vocabulary as retrieval.
        """

        issues: list[EvaluationIssue] = []
        for day in plan.itinerary:
            issues.extend(self._evaluate_schedule(day))
            issues.extend(self._evaluate_routes(day))
        issues.extend(
            self._evaluate_preferences(
                plan,
                requirement.interests,
                interest_tags_by_poi=interest_tags_by_poi,
            )
        )
        issues = self._deduplicate_issues(issues)
        hard_issue_exists = any(issue.severity == "high" for issue in issues)
        return EvaluationResult(
            passed=not hard_issue_exists,
            issues=issues,
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

    def _evaluate_preferences(
        self,
        plan: TravelPlan,
        interests: list[str],
        *,
        interest_tags_by_poi: Mapping[str, Sequence[str]] | None = None,
    ) -> list[EvaluationIssue]:
        if not interests:
            return []
        if interest_tags_by_poi is not None:
            covered = sum(
                any(
                    _interest_matches_stop(
                        interest,
                        stop,
                        interest_tags_by_poi=interest_tags_by_poi,
                    )
                    for day in plan.itinerary
                    for stop in day.attractions
                )
                for interest in interests
            )
        else:
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


def _interest_matches_tags(interest: str, tags: Sequence[str]) -> bool:
    normalized = interest.strip().lower()
    aliases = next(
        (values for key, values in _INTEREST_ALIASES.items() if key in normalized),
        (normalized,),
    )
    clean_tags = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    return any(
        alias.lower() == tag or alias.lower() in tag or tag in alias.lower()
        for alias in aliases
        for tag in clean_tags
    )


def _interest_matches_stop(interest: str, stop, *, interest_tags_by_poi: Mapping[str, Sequence[str]]) -> bool:
    tags = interest_tags_by_poi.get(stop.poi_id, ())
    if tags:
        return _interest_matches_tags(interest, tags)
    # Historical or partially enriched plans may not have a tag entry. Keep
    # the old text-based fallback for those individual stops only.
    searchable = f"{stop.name} {stop.category or ''} {stop.description or ''}".lower()
    return _interest_matches(interest, searchable)
