"""Deterministic distance-aware attraction ordering and route feasibility."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from backend.attractions.dedupe import dedupe_attractions
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.planning import CandidateDecision, PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.schedule.service import DAY_END_MINUTES, ScheduleBuilder


EARTH_RADIUS_M = 6_371_000.0
PREFERENCE_WEIGHT = 0.7
DISTANCE_WEIGHT = 0.3
DEFAULT_DAY_START = "08:00"
DEFAULT_DAY_END = "21:00"


class DynamicSchedulingError(ValueError):
    """Raised when a city cannot produce a valid deterministic plan."""


def haversine_distance_m(
    origin_longitude: float | None,
    origin_latitude: float | None,
    destination_longitude: float | None,
    destination_latitude: float | None,
) -> float | None:
    """Return the great-circle distance in metres, or ``None`` for bad data."""

    values = (
        origin_longitude,
        origin_latitude,
        destination_longitude,
        destination_latitude,
    )
    if any(value is None for value in values):
        return None
    try:
        origin_lon, origin_lat, destination_lon, destination_lat = (
            float(value) for value in values
        )
    except (TypeError, ValueError):
        return None
    if not all(
        math.isfinite(value)
        for value in (origin_lon, origin_lat, destination_lon, destination_lat)
    ):
        return None
    if not (-180 <= origin_lon <= 180 and -180 <= destination_lon <= 180):
        return None
    if not (-90 <= origin_lat <= 90 and -90 <= destination_lat <= 90):
        return None

    longitude_delta = math.radians(destination_lon - origin_lon)
    latitude_delta = math.radians(destination_lat - origin_lat)
    origin_latitude_rad = math.radians(origin_lat)
    destination_latitude_rad = math.radians(destination_lat)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude_rad)
        * math.cos(destination_latitude_rad)
        * math.sin(longitude_delta / 2) ** 2
    )
    # Floating point round-off can push the value a tiny amount outside [0, 1].
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


def calculate_distance_score(
    distance_m: float | None,
    max_distance_m: float | None,
) -> float:
    """Normalize a distance so nearer candidates receive the larger score."""

    if distance_m is None or max_distance_m is None:
        return 0.0
    if max_distance_m <= 0:
        # All reachable candidates are at the same location.  They are equally
        # near, so give them the maximum distance score without dividing by 0.
        return 1.0
    return max(0.0, min(1.0, 1.0 - distance_m / max_distance_m))


def calculate_priority(preference_score: float, distance_score: float) -> float:
    """Apply the report-defined 0.7/0.3 priority formula."""

    return PREFERENCE_WEIGHT * max(0.0, preference_score) + DISTANCE_WEIGHT * max(
        0.0, distance_score
    )


@dataclass(frozen=True)
class DynamicPlanningResult:
    city_plan: CityPlan
    decisions: list[CandidateDecision]


@dataclass(frozen=True)
class RouteAwarePlanningResult:
    """A city plan plus routes validated before each attraction was selected."""

    city_plan: CityPlan
    routes_by_day: list[list[RouteSegment]]
    decisions: list[CandidateDecision]


@dataclass(frozen=True)
class _ScoredCandidate:
    candidate: PlanningCandidate
    original_index: int
    distance_m: float | None
    distance_score: float
    priority_score: float


class DynamicCityScheduler:
    """Select candidates by preference and changing current position.

    The first version uses visit duration, meal time, and a small inter-stop
    buffer as a local capacity estimate.  Real route duration is deliberately
    left to the next stage, where route feasibility can reject and replace a
    candidate before it enters the final itinerary.
    """

    def __init__(
        self,
        *,
        meal_minutes: int = 40,
        buffer_minutes: int = 20,
        day_start_time: str = DEFAULT_DAY_START,
        day_end_time: str = DEFAULT_DAY_END,
    ):
        self.meal_minutes = max(0, int(meal_minutes))
        self.buffer_minutes = max(0, int(buffer_minutes))
        self.day_start_minutes = _parse_time(day_start_time)
        self.day_end_minutes = _parse_time(day_end_time)
        if self.day_start_minutes >= self.day_end_minutes:
            raise ValueError("每日结束时间必须晚于开始时间")

    def plan(
        self,
        *,
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: Iterable[PlanningCandidate],
    ) -> DynamicPlanningResult:
        unique_candidates = _unique_candidates(candidates, city_stay.city)

        start_minutes = _parse_time(requirement.daily_start_time or _format_time(self.day_start_minutes))
        end_minutes = _parse_time(requirement.daily_end_time or _format_time(self.day_end_minutes))
        if start_minutes >= end_minutes:
            raise DynamicSchedulingError("每日结束时间必须晚于开始时间")

        unused = list(unique_candidates)
        decisions: list[CandidateDecision] = []
        day_plans: list[DayPlan] = []

        for day in range(1, city_stay.days + 1):
            selected: list[PlanningCandidate] = []
            elapsed = 0
            lunch_taken = False
            # There is no configured hotel/start coordinate yet, so every day
            # deliberately chooses its first stop from preference alone.
            previous_selected: PlanningCandidate | None = None

            while unused:
                # Reserve at least one unique candidate for every future day
                # whenever the pool is large enough. This keeps a five-POI,
                # three-day request from consuming the entire pool on day one.
                if selected and len(unused) <= city_stay.days - day:
                    break
                ranked = self._rank(unused, previous_selected)
                chosen: _ScoredCandidate | None = None
                for scored in ranked:
                    additional, would_take_lunch = self._additional_minutes(
                        scored.candidate,
                        elapsed=elapsed,
                        start_minutes=start_minutes,
                        stop_count=len(selected),
                        lunch_taken=lunch_taken,
                    )
                    feasible = elapsed + additional <= end_minutes - start_minutes
                    if feasible:
                        chosen = scored
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=scored,
                                feasible=True,
                                selected=True,
                            )
                        )
                        elapsed += additional
                        lunch_taken = lunch_taken or would_take_lunch
                        break
                    decisions.append(
                        self._decision(
                            day=day,
                            scored=scored,
                            feasible=False,
                            selected=False,
                            rejected_reason="time_window",
                        )
                    )

                if chosen is None:
                    if not selected:
                        # Keep the public CityPlan shape valid even when a
                        # requested window is shorter than every attraction.
                        # The trace records that this stop needs later route /
                        # schedule handling instead of hiding the problem.
                        forced = ranked[0]
                        unused.remove(forced.candidate)
                        selected.append(forced.candidate)
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=forced,
                                feasible=False,
                                selected=True,
                                rejected_reason="no_candidate_fits_window",
                            )
                        )
                        previous_selected = forced.candidate
                    break

                unused.remove(chosen.candidate)
                selected.append(chosen.candidate)
                previous_selected = chosen.candidate

            if not selected:
                # There are more requested days than unique candidates. Keep
                # compatibility with the existing CityPlan contract while
                # making the reuse explicit in the decision trace.
                fallback = unique_candidates[(day - 1) % len(unique_candidates)]
                selected.append(fallback)
                fallback_scored = self._rank([fallback], previous_selected)[0]
                decisions.append(
                    self._decision(
                        day=day,
                        scored=fallback_scored,
                        feasible=False,
                        selected=True,
                        rejected_reason="candidate_pool_exhausted_reused",
                    )
                )
                previous_selected = fallback

            preference = "、".join(requirement.interests) or "综合体验"
            day_plans.append(
                DayPlan(
                    day=day,
                    attractions=[candidate.name for candidate in selected],
                    description=(
                        f"围绕{preference}，按景点偏好匹配度和动态距离优先级安排当天游览。"
                    ),
                )
            )

        return DynamicPlanningResult(
            city_plan=CityPlan(city=city_stay.city, days=day_plans),
            decisions=decisions,
        )

    async def plan_with_routes(
        self,
        *,
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: Iterable[PlanningCandidate],
        city_adcode: str,
        route_service,
        schedule_builder: ScheduleBuilder,
        max_route_attempts_per_step: int = 5,
    ) -> RouteAwarePlanningResult:
        """Select attractions while validating each candidate's route first.

        Preference and distance scoring remain synchronous and deterministic;
        only the route lookup is asynchronous.  The route service is expected
        to provide ``resolve_segment`` and to return a ``RouteSegment`` whose
        ``duration_min`` is ``None`` when the segment is unavailable.
        """

        unique_candidates = _unique_candidates(candidates, city_stay.city)
        if not unique_candidates:
            raise DynamicSchedulingError(f"{city_stay.city} 没有可用于规划的景点知识")
        if max_route_attempts_per_step < 1:
            raise ValueError("max_route_attempts_per_step 必须大于0")
        if not hasattr(route_service, "resolve_segment"):
            raise DynamicSchedulingError("路线服务不支持单段路线校验")

        unused = list(unique_candidates)
        decisions: list[CandidateDecision] = []
        day_plans: list[DayPlan] = []
        routes_by_day: list[list[RouteSegment]] = []
        preference = "、".join(requirement.interests) or "综合体验"

        for day in range(1, city_stay.days + 1):
            selected: list[PlanningCandidate] = []
            selected_routes: list[RouteSegment] = []
            # A new day starts without a hotel or station coordinate.  The
            # first stop therefore uses preference only; distance is applied
            # again after that day's first selection.
            previous_selected: PlanningCandidate | None = None

            while unused:
                if selected and len(unused) <= city_stay.days - day:
                    break
                ranked = self._rank(unused, previous_selected)
                attempted = ranked if previous_selected is None else ranked[:max_route_attempts_per_step]
                chosen: _ScoredCandidate | None = None
                chosen_route: RouteSegment | None = None

                for scored in attempted:
                    trial_candidates = [*selected, scored.candidate]
                    if not self._rough_visit_fits(
                        trial_candidates,
                        requirement=requirement,
                        schedule_builder=schedule_builder,
                    ):
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=scored,
                                feasible=False,
                                selected=False,
                                rejected_reason="time_window",
                            )
                        )
                        continue
                    route: RouteSegment | None = None
                    trial_routes = list(selected_routes)
                    if previous_selected is not None:
                        try:
                            route = await route_service.resolve_segment(
                                _candidate_stop(previous_selected),
                                _candidate_stop(scored.candidate),
                                requested_mode=requirement.transport_mode,
                                city_adcode=city_adcode,
                            )
                        except Exception:
                            route = None
                        if route is None or route.duration_min is None:
                            decisions.append(
                                self._decision(
                                    day=day,
                                    scored=scored,
                                    feasible=False,
                                    selected=False,
                                    rejected_reason="route_unavailable",
                                )
                            )
                            continue
                        trial_routes.append(route)

                    trial_day = _trial_day(
                        city_stay=city_stay,
                        day=day,
                        candidates=trial_candidates,
                        routes=trial_routes,
                        description=f"围绕{preference}安排当天游览。",
                    )
                    if schedule_builder.try_build_day_exact(
                        trial_day,
                        day_start_time=requirement.daily_start_time,
                        day_end_time=requirement.daily_end_time,
                    ) is None:
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=scored,
                                feasible=False,
                                selected=False,
                                rejected_reason="time_window",
                            )
                        )
                        continue

                    chosen = scored
                    chosen_route = route
                    decisions.append(
                        self._decision(
                            day=day,
                            scored=scored,
                            feasible=True,
                            selected=True,
                        )
                    )
                    break

                if chosen is None:
                    if not selected:
                        raise DynamicSchedulingError(
                            f"{city_stay.city} 第{day}天没有符合路线和时间窗口的景点"
                        )
                    break

                unused.remove(chosen.candidate)
                selected.append(chosen.candidate)
                if chosen_route is not None:
                    selected_routes.append(chosen_route)
                previous_selected = chosen.candidate

            if not selected:
                fallback = unique_candidates[(day - 1) % len(unique_candidates)]
                trial_day = _trial_day(
                    city_stay=city_stay,
                    day=day,
                    candidates=[fallback],
                    routes=[],
                    description=f"围绕{preference}安排当天游览。",
                )
                if schedule_builder.try_build_day_exact(
                    trial_day,
                    day_start_time=requirement.daily_start_time,
                    day_end_time=requirement.daily_end_time,
                ) is None:
                    raise DynamicSchedulingError(
                        f"{city_stay.city} 第{day}天没有符合时间窗口的景点"
                    )
                selected.append(fallback)
                fallback_scored = self._rank([fallback], previous_selected)[0]
                decisions.append(
                    self._decision(
                        day=day,
                        scored=fallback_scored,
                        feasible=False,
                        selected=True,
                        rejected_reason="candidate_pool_exhausted_reused",
                    )
                )
                previous_selected = fallback

            day_plans.append(
                DayPlan(
                    day=day,
                    attractions=[candidate.name for candidate in selected],
                    description=(
                        f"围绕{preference}，按偏好匹配、动态距离和路线可行性安排当天游览。"
                    ),
                )
            )
            routes_by_day.append(selected_routes)

        return RouteAwarePlanningResult(
            city_plan=CityPlan(city=city_stay.city, days=day_plans),
            routes_by_day=routes_by_day,
            decisions=decisions,
        )

    def _rank(
        self,
        candidates: list[PlanningCandidate],
        current: PlanningCandidate | None,
    ) -> list[_ScoredCandidate]:
        distances: list[float | None] = []
        for candidate in candidates:
            distances.append(
                haversine_distance_m(
                    getattr(current, "longitude", None),
                    getattr(current, "latitude", None),
                    candidate.longitude,
                    candidate.latitude,
                )
                if current is not None
                else None
            )
        reachable = [distance for distance in distances if distance is not None]
        max_distance = max(reachable, default=None)
        scored = [
            _ScoredCandidate(
                candidate=candidate,
                original_index=index,
                distance_m=distance,
                distance_score=(
                    calculate_distance_score(distance, max_distance)
                    if current is not None
                    else 0.0
                ),
                priority_score=calculate_priority(
                    float(getattr(candidate, "preference_score", 0.0) or 0.0),
                    (
                        calculate_distance_score(distance, max_distance)
                        if current is not None
                        else 0.0
                    ),
                ),
            )
            for index, (candidate, distance) in enumerate(zip(candidates, distances, strict=True))
        ]
        scored.sort(
            key=lambda item: (
                -item.priority_score,
                -float(getattr(item.candidate, "preference_score", 0.0) or 0.0),
                -int(getattr(item.candidate, "tag_match_count", 0) or 0),
                -(1 if item.distance_m is not None else 0),
                item.original_index,
            )
        )
        return scored

    @staticmethod
    def _rough_visit_fits(
        candidates: list[PlanningCandidate],
        *,
        requirement: TravelRequirement,
        schedule_builder: ScheduleBuilder,
    ) -> bool:
        """Reject impossible visit-only combinations before a route call."""

        start = (
            _parse_time(requirement.daily_start_time)
            if requirement.daily_start_time
            else schedule_builder.day_start_minutes
        )
        end = (
            _parse_time(requirement.daily_end_time)
            if requirement.daily_end_time
            else DAY_END_MINUTES
        )
        visit_minutes = sum(
            max(1, int(candidate.visit_duration_minutes or 90))
            for candidate in candidates
        )
        buffer_minutes = schedule_builder.buffer_minutes * max(0, len(candidates) - 1)
        return visit_minutes + buffer_minutes <= end - start

    def _additional_minutes(
        self,
        candidate: PlanningCandidate,
        *,
        elapsed: int,
        start_minutes: int,
        stop_count: int,
        lunch_taken: bool,
    ) -> tuple[int, bool]:
        duration = max(1, int(candidate.visit_duration_minutes or 90))
        buffer = self.buffer_minutes if stop_count else 0
        current = start_minutes + elapsed + buffer
        end = current + duration
        meal = 0
        if not lunch_taken and self.meal_minutes and end >= 12 * 60:
            meal = self.meal_minutes
        return buffer + duration + meal, meal > 0

    @staticmethod
    def _decision(
        *,
        day: int,
        scored: _ScoredCandidate,
        feasible: bool,
        selected: bool,
        rejected_reason: str | None = None,
    ) -> CandidateDecision:
        return CandidateDecision(
            day=day,
            poi_id=scored.candidate.poi_id,
            name=scored.candidate.name,
            preference_score=float(
                getattr(scored.candidate, "preference_score", 0.0) or 0.0
            ),
            distance_m=scored.distance_m,
            distance_score=scored.distance_score,
            priority_score=scored.priority_score,
            feasible=feasible,
            selected=selected,
            rejected_reason=rejected_reason,
        )


def dynamic_city_plan(
    *,
    city_stay: CityStay,
    requirement: TravelRequirement,
    candidates: Iterable[PlanningCandidate],
) -> DynamicPlanningResult:
    """Convenience wrapper for the default deterministic scheduler."""

    return DynamicCityScheduler().plan(
        city_stay=city_stay,
        requirement=requirement,
        candidates=candidates,
    )


async def dynamic_city_plan_with_routes(
    *,
    city_stay: CityStay,
    requirement: TravelRequirement,
    candidates: Iterable[PlanningCandidate],
    city_adcode: str,
    route_service,
    schedule_builder: ScheduleBuilder,
    max_route_attempts_per_step: int = 5,
) -> RouteAwarePlanningResult:
    """Convenience wrapper for route-aware dynamic scheduling."""

    return await DynamicCityScheduler().plan_with_routes(
        city_stay=city_stay,
        requirement=requirement,
        candidates=candidates,
        city_adcode=city_adcode,
        route_service=route_service,
        schedule_builder=schedule_builder,
        max_route_attempts_per_step=max_route_attempts_per_step,
    )


def _unique_candidates(
    candidates: Iterable[PlanningCandidate],
    city: str,
) -> list[PlanningCandidate]:
    unique_candidates = [
        candidate
        for candidate in dedupe_attractions(list(candidates))
        if str(getattr(candidate, "name", "") or "").strip()
    ]
    if not unique_candidates:
        raise DynamicSchedulingError(f"{city} 没有可用于规划的景点知识")
    return unique_candidates


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


def _trial_day(
    *,
    city_stay: CityStay,
    day: int,
    candidates: list[PlanningCandidate],
    routes: list[RouteSegment],
    description: str,
) -> TravelDay:
    return TravelDay(
        day=day,
        city=city_stay.city,
        city_adcode="",
        city_day=day,
        attractions=[_candidate_stop(candidate) for candidate in candidates],
        routes=routes,
        description=description,
    )


def _parse_time(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("时间必须使用 HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间必须使用 HH:MM")
    return hour * 60 + minute


def _format_time(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


__all__ = [
    "DEFAULT_DAY_END",
    "DEFAULT_DAY_START",
    "DISTANCE_WEIGHT",
    "DynamicCityScheduler",
    "DynamicPlanningResult",
    "RouteAwarePlanningResult",
    "DynamicSchedulingError",
    "EARTH_RADIUS_M",
    "PREFERENCE_WEIGHT",
    "calculate_distance_score",
    "calculate_priority",
    "dynamic_city_plan",
    "dynamic_city_plan_with_routes",
    "haversine_distance_m",
]
