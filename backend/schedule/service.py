from backend.models.schedule import DaySchedule
from backend.models.travel_plan import TravelDay


class ScheduleBuilder:
    def __init__(
        self,
        *,
        day_start_time: str = "09:00",
        meal_minutes: int = 90,
        buffer_minutes: int = 60,
        fallback_visit_minutes: int = 90,
    ):
        self.day_start_minutes = _parse_time(day_start_time)
        self.meal_minutes = meal_minutes
        self.buffer_minutes = buffer_minutes
        self.fallback_visit_minutes = fallback_visit_minutes

    def build_day(self, day: TravelDay) -> TravelDay:
        visit_minutes = sum(
            stop.estimated_visit_minutes or self.fallback_visit_minutes
            for stop in day.attractions
        )
        available_route_minutes = [route.duration_min for route in day.routes if route.duration_min]
        route_minutes = sum(available_route_minutes)
        total_minutes = visit_minutes + route_minutes + self.meal_minutes + self.buffer_minutes

        status = "complete"
        if any(route.duration_min is None for route in day.routes):
            status = "incomplete"
        elif any(route.estimated for route in day.routes) or any(
            stop.duration_source == "category_fallback" for stop in day.attractions
        ):
            status = "estimated"

        current = self.day_start_minutes
        lunch_taken = False
        stop_count = len(day.attractions)
        buffer_base, buffer_remainder = divmod(self.buffer_minutes, stop_count)
        scheduled_stops = []
        for index, stop in enumerate(day.attractions):
            if index > 0:
                current += day.routes[index - 1].duration_min or 0
            if not lunch_taken and current >= 12 * 60:
                current += self.meal_minutes
                lunch_taken = True
            duration = stop.estimated_visit_minutes or self.fallback_visit_minutes
            start = current
            end = start + duration
            scheduled_stops.append(
                stop.model_copy(update={"start_time": _format_time(start), "end_time": _format_time(end)})
            )
            current = end + buffer_base + (1 if index < buffer_remainder else 0)
            if not lunch_taken and current >= 12 * 60 and index < stop_count - 1:
                current += self.meal_minutes
                lunch_taken = True
        if not lunch_taken:
            current += self.meal_minutes

        schedule = DaySchedule(
            start_time=_format_time(self.day_start_minutes),
            end_time=_format_time(current),
            visit_minutes=visit_minutes,
            route_minutes=route_minutes,
            meal_minutes=self.meal_minutes,
            buffer_minutes=self.buffer_minutes,
            total_minutes=total_minutes,
            status=status,
        )
        return day.model_copy(update={"attractions": scheduled_stops, "schedule": schedule})


def _parse_time(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("day_start_time 必须使用 HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("day_start_time 必须使用 HH:MM")
    return hour * 60 + minute


def _format_time(total_minutes: int) -> str:
    day_offset, clock_minutes = divmod(total_minutes, 24 * 60)
    hour, minute = divmod(clock_minutes, 60)
    prefix = "" if day_offset == 0 else ("次日 " if day_offset == 1 else f"第{day_offset + 1}日 ")
    return f"{prefix}{hour:02d}:{minute:02d}"
