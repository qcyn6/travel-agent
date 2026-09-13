from collections.abc import Callable
from datetime import date

from backend.models.experiment import BaselinePlan, ExperimentCase, ExperimentMetrics
from backend.models.travel_plan import TravelPlanResponse


def evaluate_baseline_plan(
    plan: BaselinePlan,
    case: ExperimentCase,
    *,
    attraction_exists: Callable[[str, str], bool],
    max_day_minutes: int = 600,
) -> ExperimentMetrics:
    attractions = [attraction for day in plan.days for attraction in day.attractions]
    valid = sum(
        attraction_exists(day.city, attraction.name)
        for day in plan.days
        for attraction in day.attractions
    )
    searchable = " ".join(attraction.name for attraction in attractions).lower()
    feasible_days = sum(
        sum(attraction.estimated_visit_minutes for attraction in day.attractions) + 150
        <= max_day_minutes
        for day in plan.days
    )
    return ExperimentMetrics(
        attraction_validity=_ratio(valid, len(attractions)),
        interest_coverage=_interest_coverage(case.interests, searchable),
        schedule_feasibility=_ratio(feasible_days, len(plan.days)),
        route_reasonableness=None,
        external_constraint_satisfaction=None,
        notes=["直接生成基线没有真实路线和外部约束数据，这两项不评分"],
    )


def evaluate_system_plan(
    result: TravelPlanResponse,
    case: ExperimentCase,
    *,
    attraction_exists: Callable[[str, str], bool],
) -> ExperimentMetrics:
    days = result.plan.itinerary
    attractions = [stop for day in days for stop in day.attractions]
    valid = sum(
        bool(
            stop.poi_id
            and stop.city == day.city
            and stop.longitude is not None
            and stop.latitude is not None
            and attraction_exists(day.city, stop.name)
        )
        for day in days
        for stop in day.attractions
    )
    searchable = " ".join(
        f"{stop.name} {stop.category or ''} {stop.description or ''}" for stop in attractions
    ).lower()
    feasible_days = sum(
        day.schedule is not None and day.schedule.total_minutes <= 600 for day in days
    )
    reasonable_route_days = sum(_routes_are_reasonable(day) for day in days)

    external_checks = 0
    external_satisfied = 0
    for day in days:
        if day.weather is not None and day.weather.status == "available":
            external_checks += 1
            external_satisfied += not _weather_conflicts(day)
        for stop in day.attractions:
            if stop.opening_hours is None or stop.opening_hours.status != "known":
                continue
            external_checks += 1
            external_satisfied += _opening_hours_satisfied(stop, day.date)
    external_score = (
        _ratio(external_satisfied, external_checks) if external_checks else None
    )
    notes = [] if external_checks else ["本用例没有可评估的天气或已知开放时间"]
    return ExperimentMetrics(
        attraction_validity=_ratio(valid, len(attractions)),
        interest_coverage=_interest_coverage(case.interests, searchable),
        schedule_feasibility=_ratio(feasible_days, len(days)),
        route_reasonableness=_ratio(reasonable_route_days, len(days)),
        external_constraint_satisfaction=external_score,
        notes=notes,
    )


def _interest_coverage(interests: list[str], searchable: str) -> float:
    return _ratio(sum(_interest_matches(interest, searchable) for interest in interests), len(interests))


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


_INTEREST_ALIASES = {
    "摄影": ("摄影", "拍照", "风景", "古城", "古镇"),
    "历史": ("历史", "文化", "遗址", "古城", "古镇", "博物馆"),
    "文化": ("文化", "人文", "历史", "博物馆", "古城", "古镇"),
    "人文": ("人文", "文化", "历史", "博物馆"),
    "自然": ("自然", "山", "湖", "湿地", "森林", "公园", "风景"),
    "海滨": ("海滨", "海", "沙滩", "海岸"),
    "美食": ("美食", "餐饮", "小吃"),
}


def _interest_matches(interest: str, searchable: str) -> bool:
    normalized = interest.strip().lower()
    aliases = next(
        (values for key, values in _INTEREST_ALIASES.items() if key in normalized),
        (normalized,),
    )
    return any(alias in searchable for alias in aliases)


def _routes_are_reasonable(day) -> bool:
    expected_segments = max(len(day.attractions) - 1, 0)
    if len(day.routes) != expected_segments:
        return False
    durations = [route.duration_min for route in day.routes]
    return all(value is not None and value <= 60 for value in durations) and sum(
        value or 0 for value in durations
    ) <= 180


def _weather_conflicts(day) -> bool:
    weather = day.weather
    if weather is None:
        return False
    rainy = (
        weather.precipitation_probability is not None
        and weather.precipitation_probability >= 0.5
    ) or any(word in (weather.condition or "") for word in ("雨", "雪", "雷"))
    outdoor_weight = sum(
        1 if stop.environment == "outdoor" else 0.5 if stop.environment == "mixed" else 0
        for stop in day.attractions
    )
    return rainy and outdoor_weight / len(day.attractions) > 0.5


def _opening_hours_satisfied(stop, target_date: date | None) -> bool:
    arrival = _clock_minutes(stop.start_time)
    if arrival is None:
        return False
    periods = stop.opening_hours.periods
    if target_date is not None:
        weekday = target_date.isoweekday()
        periods = [period for period in periods if not period.weekdays or weekday in period.weekdays]
    return any(
        (_clock_minutes(period.open_time) or 0)
        <= arrival
        <= (_clock_minutes(period.last_entry_time or period.close_time) or 0)
        for period in periods
    )


def _clock_minutes(value: str | None) -> int | None:
    if not value or "日" in value:
        return None
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError:
        return None
    return hour * 60 + minute
