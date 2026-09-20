from dataclasses import dataclass

from backend.models.route import RouteSegment
from backend.models.schedule import DaySchedule
from backend.models.travel_plan import AttractionStop, TravelDay


DAY_START_MINUTES = 8 * 60
DAY_END_MINUTES = 21 * 60


@dataclass(frozen=True)
class _ScheduleResult:
    stops: list[AttractionStop]
    routes: list[RouteSegment]
    schedule: DaySchedule
    fits: bool


class ScheduleBuilder:
    def __init__(
        self,
        *,
        day_start_time: str = "08:00",
        meal_minutes: int = 40,
        buffer_minutes: int = 20,
        fallback_visit_minutes: int = 90,
    ):
        configured_start = _parse_time(day_start_time)
        if configured_start >= DAY_END_MINUTES:
            raise ValueError("day_start_time 必须早于21:00")
        self.day_start_minutes = configured_start
        self.meal_minutes = max(0, meal_minutes)
        self.buffer_minutes = max(0, buffer_minutes)
        self.fallback_visit_minutes = max(1, fallback_visit_minutes)

    def build_day(
        self,
        day: TravelDay,
        *,
        day_start_time: str | None = None,
        day_end_time: str | None = None,
    ) -> TravelDay:
        window_start = (
            _parse_time(day_start_time)
            if day_start_time is not None
            else self.day_start_minutes
        )
        window_end = (
            _parse_time(day_end_time)
            if day_end_time is not None
            else DAY_END_MINUTES
        )
        _validate_window(window_start, window_end)

        # Find the longest prefix that can be completed inside the user's
        # same-day window. A later attraction is never allowed to spill into
        # the next day; it is omitted from this day's plan instead.
        selected: _ScheduleResult | None = None
        for count in range(1, len(day.attractions) + 1):
            candidate = self._simulate(
                day.attractions[:count],
                day.routes[: max(0, count - 1)],
                day_start_minutes=window_start,
                day_end_minutes=window_end,
            )
            if candidate.fits:
                selected = candidate

        if selected is None:
            raise ValueError("每日时间窗口不足以安排当天的景点，请延长游玩时间")

        return self._apply_result(day, selected)

    def build_day_exact(
        self,
        day: TravelDay,
        *,
        day_start_time: str | None = None,
        day_end_time: str | None = None,
    ) -> TravelDay:
        """Build a day only when every supplied stop and route fits.

        ``build_day`` remains backwards compatible and may trim a legacy
        itinerary to its longest valid prefix.  The dynamic scheduler uses
        this strict variant so a candidate is accepted only after its route,
        visit duration, meal and buffer have all been validated together.
        """

        window_start = (
            _parse_time(day_start_time)
            if day_start_time is not None
            else self.day_start_minutes
        )
        window_end = (
            _parse_time(day_end_time)
            if day_end_time is not None
            else DAY_END_MINUTES
        )
        _validate_window(window_start, window_end)
        expected_route_count = max(0, len(day.attractions) - 1)
        if len(day.routes) != expected_route_count:
            raise ValueError("严格时间校验要求每一段相邻景点都有路线")
        if any(route.duration_min is None for route in day.routes):
            raise ValueError("严格时间校验无法接受没有耗时的路线")

        result = self._simulate(
            day.attractions,
            day.routes,
            day_start_minutes=window_start,
            day_end_minutes=window_end,
        )
        if not result.fits:
            raise ValueError("每日时间窗口不足以安排当天的全部景点")
        return self._apply_result(day, result)

    def try_build_day_exact(
        self,
        day: TravelDay,
        *,
        day_start_time: str | None = None,
        day_end_time: str | None = None,
    ) -> TravelDay | None:
        """Return a strictly scheduled day, or ``None`` when it does not fit."""

        try:
            return self.build_day_exact(
                day,
                day_start_time=day_start_time,
                day_end_time=day_end_time,
            )
        except ValueError:
            return None

    @staticmethod
    def _apply_result(day: TravelDay, selected: _ScheduleResult) -> TravelDay:
        return day.model_copy(
            update={
                "attractions": selected.stops,
                "routes": selected.routes,
                "schedule": selected.schedule,
            }
        )

    def _simulate(
        self,
        stops: list[AttractionStop],
        routes: list[RouteSegment],
        *,
        day_start_minutes: int,
        day_end_minutes: int,
    ) -> _ScheduleResult:
        current = day_start_minutes
        lunch_taken = False
        scheduled_stops: list[AttractionStop] = []
        actual_route_minutes = 0
        actual_meal_minutes = 0
        actual_buffer_minutes = 0
        fits = True

        for index, stop in enumerate(stops):
            if index > 0:
                route = routes[index - 1] if index - 1 < len(routes) else None
                if route is not None and route.duration_min:
                    current += route.duration_min
                    actual_route_minutes += route.duration_min

            if not lunch_taken and current >= 12 * 60 and self.meal_minutes:
                meal_end = current + self.meal_minutes
                if meal_end <= day_end_minutes:
                    current = meal_end
                    actual_meal_minutes += self.meal_minutes
                    lunch_taken = True

            duration = stop.visit_duration_minutes or self.fallback_visit_minutes
            start = current
            end = start + duration
            if start < day_start_minutes or end > day_end_minutes:
                fits = False
                break
            scheduled_stops.append(
                stop.model_copy(
                    update={
                        "start_time": _format_time(start),
                        "end_time": _format_time(end),
                    }
                )
            )
            current = end

            # The configured buffer is a fixed gap between adjacent
            # attractions. The final attraction does not need one after it.
            buffer = self.buffer_minutes if index < len(stops) - 1 else 0
            available = max(0, day_end_minutes - current)
            used_buffer = min(buffer, available)
            current += used_buffer
            actual_buffer_minutes += used_buffer

            if (
                not lunch_taken
                and current >= 12 * 60
                and index < len(stops) - 1
                and self.meal_minutes
            ):
                meal_end = current + self.meal_minutes
                if meal_end <= day_end_minutes:
                    current = meal_end
                    actual_meal_minutes += self.meal_minutes
                    lunch_taken = True

        if not lunch_taken and self.meal_minutes:
            available = max(0, day_end_minutes - current)
            actual_meal_minutes += min(self.meal_minutes, available)
            current += min(self.meal_minutes, available)

        visit_minutes = sum(
            stop.visit_duration_minutes or self.fallback_visit_minutes
            for stop in stops[: len(scheduled_stops)]
        )
        selected_routes = routes[: max(0, len(scheduled_stops) - 1)]
        status = "complete"
        if any(route.duration_min is None for route in selected_routes):
            status = "incomplete"
        elif any(route.estimated for route in selected_routes):
            status = "estimated"

        schedule = DaySchedule(
            start_time=_format_time(day_start_minutes),
            end_time=_format_time(min(current, day_end_minutes)),
            visit_minutes=visit_minutes,
            route_minutes=actual_route_minutes,
            meal_minutes=actual_meal_minutes,
            buffer_minutes=actual_buffer_minutes,
            total_minutes=(
                visit_minutes
                + actual_route_minutes
                + actual_meal_minutes
                + actual_buffer_minutes
            ),
            status=status,
        )
        return _ScheduleResult(
            stops=scheduled_stops,
            routes=selected_routes,
            schedule=schedule,
            fits=fits and len(scheduled_stops) == len(stops),
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


def _validate_window(start_minutes: int, end_minutes: int) -> None:
    if start_minutes >= end_minutes:
        raise ValueError("每日结束时间必须晚于开始时间，且不支持跨日安排")


def _format_time(total_minutes: int) -> str:
    # New schedules are bounded before formatting. Keep the plain clock format
    # here so a valid plan can never display a “次日” prefix.
    _, clock_minutes = divmod(max(0, total_minutes), 24 * 60)
    hour, minute = divmod(clock_minutes, 60)
    return f"{hour:02d}:{minute:02d}"
