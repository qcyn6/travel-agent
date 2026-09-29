"""Deterministic distance-aware attraction ordering and route feasibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import permutations
import math
from typing import Iterable

from backend.attractions.dedupe import dedupe_attractions
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.planning import CandidateDecision, PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.models.route import RouteSegment
from backend.models.travel_plan import AttractionStop, TravelDay
from backend.schedule.opening_hours import visit_fits_opening_hours
from backend.schedule.service import DAY_END_MINUTES, ScheduleBuilder


EARTH_RADIUS_M = 6_371_000.0
PREFERENCE_WEIGHT = 0.4
DISTANCE_WEIGHT = 0.6
DEFAULT_DAY_START = "08:00"
DEFAULT_DAY_END = "21:00"
_EXACT_ROUTE_ORDER_MAX_STOPS = 8


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


def calculate_priority(
    preference_score: float,
    distance_score: float,
    *,
    preference_weight: float = PREFERENCE_WEIGHT,
    distance_weight: float = DISTANCE_WEIGHT,
) -> float:
    """Apply configurable preference/distance weights.

    The production defaults favor route proximity at ``0.4/0.6`` (preference /
    distance). Experiments can vary weights without changing this default.
    """

    return preference_weight * max(0.0, preference_score) + distance_weight * max(
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
    """Select candidates by one continuous city-level ranking chain.

    The first stop of a city is preference-first.  Every later stop, including
    the first stop of a later day, is ranked from the previous selected
    attraction using preference and distance.  Visit duration, meal time, and
    the daily buffer remain the local capacity estimate; route-aware planning
    additionally validates real route feasibility before accepting a stop.
    """

    def __init__(
        self,
        *,
        meal_minutes: int = 40,
        buffer_minutes: int = 20,
        day_start_time: str = DEFAULT_DAY_START,
        day_end_time: str = DEFAULT_DAY_END,
        preference_weight: float = PREFERENCE_WEIGHT,
        distance_weight: float = DISTANCE_WEIGHT,
        optimize_day_order: bool = True,
    ):
        self.meal_minutes = max(0, int(meal_minutes))
        self.buffer_minutes = max(0, int(buffer_minutes))
        self.day_start_minutes = _parse_time(day_start_time)
        self.day_end_minutes = _parse_time(day_end_time)
        if self.day_start_minutes >= self.day_end_minutes:
            raise ValueError("每日结束时间必须晚于开始时间")
        if preference_weight < 0 or distance_weight < 0:
            raise ValueError("评分权重不能为负数")
        if preference_weight == 0 and distance_weight == 0:
            raise ValueError("至少需要一个非零评分权重")
        self.preference_weight = float(preference_weight)
        self.distance_weight = float(distance_weight)
        # Retain this constructor option for callers built against the former
        # implementation.  The production rule is now always one continuous
        # preference/distance chain rather than a separate per-day optimizer.
        self.optimize_day_order = bool(optimize_day_order)

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

        # Keep one continuous city-level ranking chain.  A new day resets the
        # time budget, but not the previous attraction used for distance
        # scoring.
        previous_selected: PlanningCandidate | None = None
        for day in range(1, city_stay.days + 1):
            visit_date = (
                requirement.start_date + timedelta(days=day - 1)
                if requirement.start_date is not None
                else None
            )
            selected: list[PlanningCandidate] = []
            elapsed = 0
            lunch_taken = False

            while unused:
                # Reserve at least one unique candidate for every future day
                # whenever the pool is large enough. This keeps a five-POI,
                # three-day request from consuming the entire pool on day one.
                if selected and len(unused) <= city_stay.days - day:
                    break
                ranked = self._rank(unused, previous_selected)
                chosen: _ScoredCandidate | None = None
                for scored in ranked:
                    visit_start = (
                        start_minutes
                        + elapsed
                        + (self.buffer_minutes if selected else 0)
                    )
                    opening_fits = visit_fits_opening_hours(
                        scored.candidate.opening_hours_weekly,
                        visit_date,
                        visit_start,
                        max(1, int(scored.candidate.visit_duration_minutes or 90)),
                    )
                    if not opening_fits:
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=scored,
                                feasible=False,
                                selected=False,
                                rejected_reason="opening_hours",
                            )
                        )
                        continue
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
                        forced = next(
                            (
                                scored
                                for scored in ranked
                                if visit_fits_opening_hours(
                                    scored.candidate.opening_hours_weekly,
                                    visit_date,
                                    start_minutes,
                                    max(
                                        1,
                                        int(scored.candidate.visit_duration_minutes or 90),
                                    ),
                                )
                            ),
                            None,
                        )
                        if forced is None:
                            raise DynamicSchedulingError(
                                f"{city_stay.city} 第{day}天没有营业时间匹配的景点"
                            )
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
        max_route_attempts_per_step: int | None = None,
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
        if max_route_attempts_per_step is not None and max_route_attempts_per_step < 1:
            raise ValueError("max_route_attempts_per_step 必须大于0")
        if not hasattr(route_service, "resolve_segment"):
            raise DynamicSchedulingError("路线服务不支持单段路线校验")

        unused = list(unique_candidates)
        decisions: list[CandidateDecision] = []
        day_plans: list[DayPlan] = []
        routes_by_day: list[list[RouteSegment]] = []
        preference = "、".join(requirement.interests) or "综合体验"

        # Keep the ranking anchor across day boundaries.  The next day's first
        # attraction is selected from the previous day's last attraction, but
        # the cross-day transfer is not part of that day's sightseeing route.
        current_location: PlanningCandidate | None = None
        for day in range(1, city_stay.days + 1):
            visit_date = (
                requirement.start_date + timedelta(days=day - 1)
                if requirement.start_date is not None
                else None
            )
            selected: list[PlanningCandidate] = []
            selected_routes: list[RouteSegment] = []
            current_time = requirement.daily_start_time or _format_time(
                schedule_builder.day_start_minutes
            )

            while unused:
                if selected and len(unused) <= city_stay.days - day:
                    break
                ranked = self._rank(unused, current_location)
                attempted = (
                    ranked
                    if not selected or max_route_attempts_per_step is None
                    else ranked[:max_route_attempts_per_step]
                )
                chosen: _ScoredCandidate | None = None
                chosen_route: RouteSegment | None = None
                chosen_schedule: TravelDay | None = None

                for scored in attempted:
                    trial_candidates = [*selected, scored.candidate]
                    if not self._quick_visit_fits(
                        scored.candidate,
                        current_time=current_time,
                        has_previous=bool(selected),
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
                    opening_fits = visit_fits_opening_hours(
                        scored.candidate.opening_hours_weekly,
                        visit_date,
                        _parse_time(current_time),
                        max(1, int(scored.candidate.visit_duration_minutes or 90)),
                    )
                    if not opening_fits:
                        decisions.append(
                            self._decision(
                                day=day,
                                scored=scored,
                                feasible=False,
                                selected=False,
                                rejected_reason="opening_hours",
                            )
                        )
                        continue
                    route: RouteSegment | None = None
                    trial_routes = list(selected_routes)
                    # The carried current_location affects the next ranking,
                    # while a route is required only between attractions on
                    # the same day.  The overnight/cross-day transfer stays
                    # outside the sightseeing schedule by design.
                    if current_location is not None and selected:
                        try:
                            route = await route_service.resolve_segment(
                                _candidate_stop(current_location),
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
                        if route.estimated or route.source not in {"amap_mcp", "amap"}:
                            decisions.append(
                                self._decision(
                                    day=day,
                                    scored=scored,
                                    feasible=False,
                                    selected=False,
                                    rejected_reason="route_not_verified",
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
                        visit_date=visit_date,
                    )
                    scheduled_trial = schedule_builder.try_build_day_exact(
                        trial_day,
                        day_start_time=requirement.daily_start_time,
                        day_end_time=requirement.daily_end_time,
                    )
                    if scheduled_trial is None:
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
                    chosen_schedule = scheduled_trial
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
                current_location = chosen.candidate
                if chosen_schedule is None or chosen_schedule.schedule is None:
                    raise DynamicSchedulingError("景点通过校验后缺少精确时间表")
                current_time = chosen_schedule.schedule.end_time

            if not selected:
                fallback = unique_candidates[(day - 1) % len(unique_candidates)]
                trial_day = _trial_day(
                    city_stay=city_stay,
                    day=day,
                    candidates=[fallback],
                    routes=[],
                    description=f"围绕{preference}安排当天游览。",
                    visit_date=visit_date,
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
                fallback_scored = self._rank([fallback], current_location)[0]
                decisions.append(
                    self._decision(
                        day=day,
                        scored=fallback_scored,
                        feasible=False,
                        selected=True,
                        rejected_reason="candidate_pool_exhausted_reused",
                    )
                )
                current_location = fallback

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
                    preference_weight=self.preference_weight,
                    distance_weight=self.distance_weight,
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
    def _quick_visit_fits(
        candidate: PlanningCandidate,
        *,
        current_time: str,
        has_previous: bool,
        requirement: TravelRequirement,
        schedule_builder: ScheduleBuilder,
    ) -> bool:
        """Reject an impossible next visit before spending a route request."""

        start = _parse_time(current_time)
        end = (
            _parse_time(requirement.daily_end_time)
            if requirement.daily_end_time
            else DAY_END_MINUTES
        )
        visit_minutes = max(1, int(candidate.visit_duration_minutes or 90))
        buffer_minutes = schedule_builder.buffer_minutes if has_previous else 0
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
    preference_weight: float = PREFERENCE_WEIGHT,
    distance_weight: float = DISTANCE_WEIGHT,
) -> DynamicPlanningResult:
    """Convenience wrapper for the default deterministic scheduler."""

    return DynamicCityScheduler(
        preference_weight=preference_weight,
        distance_weight=distance_weight,
    ).plan(
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
    max_route_attempts_per_step: int | None = None,
    preference_weight: float = PREFERENCE_WEIGHT,
    distance_weight: float = DISTANCE_WEIGHT,
) -> RouteAwarePlanningResult:
    """Convenience wrapper for route-aware dynamic scheduling."""

    return await DynamicCityScheduler(
        preference_weight=preference_weight,
        distance_weight=distance_weight,
    ).plan_with_routes(
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
        opening_hours_weekly=candidate.opening_hours_weekly,
        opening_hours_today=candidate.opening_hours_today,
        opening_hours_status=candidate.opening_hours_status,
    )


def _optimize_day_route_order(
    candidates: list[PlanningCandidate],
) -> list[PlanningCandidate]:
    """Minimize one day's local Haversine path without external API calls.

    Daily plans normally contain three to five stops, so exact permutation is
    both clearer and faster than introducing a general TSP dependency. A
    bounded nearest-neighbour fallback prevents factorial work if unusually
    short visit durations ever produce a much larger day.
    """

    if len(candidates) < 3:
        return list(candidates)
    original_index = {
        candidate.poi_id: index for index, candidate in enumerate(candidates)
    }
    distance_matrix: dict[tuple[str, str], float | None] = {}
    for origin in candidates:
        for destination in candidates:
            if origin.poi_id == destination.poi_id:
                continue
            distance_matrix[(origin.poi_id, destination.poi_id)] = haversine_distance_m(
                origin.longitude,
                origin.latitude,
                destination.longitude,
                destination.latitude,
            )

    def edge_distance(origin: PlanningCandidate, destination: PlanningCandidate) -> float:
        distance = distance_matrix.get((origin.poi_id, destination.poi_id))
        return math.inf if distance is None else distance

    def objective(order: tuple[PlanningCandidate, ...] | list[PlanningCandidate]):
        total_distance = 0.0
        for origin, destination in zip(order, order[1:]):
            distance = edge_distance(origin, destination)
            if not math.isfinite(distance):
                return (math.inf, 0.0, ())
            total_distance += distance
        # A path and its reverse have the same distance. Prefer the endpoint
        # with the stronger preference as the first stop, then stay stable.
        return (
            total_distance,
            -float(getattr(order[0], "preference_score", 0.0) or 0.0),
            tuple(original_index[item.poi_id] for item in order),
        )

    if len(candidates) <= _EXACT_ROUTE_ORDER_MAX_STOPS:
        best = min(permutations(candidates), key=objective)
        if math.isfinite(objective(best)[0]):
            return list(best)
        return list(candidates)

    possible_orders: list[list[PlanningCandidate]] = []
    for start in candidates:
        order = [start]
        remaining = [item for item in candidates if item.poi_id != start.poi_id]
        while remaining:
            current = order[-1]
            next_candidate = min(
                remaining,
                key=lambda item: (
                    edge_distance(current, item),
                    -float(getattr(item, "preference_score", 0.0) or 0.0),
                    original_index[item.poi_id],
                ),
            )
            order.append(next_candidate)
            remaining.remove(next_candidate)
        possible_orders.append(order)
    best = min(possible_orders, key=objective)
    return best if math.isfinite(objective(best)[0]) else list(candidates)


def _trial_day(
    *,
    city_stay: CityStay,
    day: int,
    candidates: list[PlanningCandidate],
    routes: list[RouteSegment],
    description: str,
    visit_date=None,
) -> TravelDay:
    return TravelDay(
        day=day,
        city=city_stay.city,
        city_adcode="",
        city_day=day,
        date=visit_date,
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
