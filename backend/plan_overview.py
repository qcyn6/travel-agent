from backend.models.itinerary import DayPlan
from backend.models.travel_plan import TravelPlan


def build_day_overview(day) -> str:
    """Build an overview from the final itinerary data, never from LLM text."""

    stop_text = "、".join(
        f"{stop.name}（{stop.category or '景点'}，约{stop.visit_duration_minutes}分钟）"
        for stop in day.attractions
    )
    visit_minutes = sum(stop.visit_duration_minutes for stop in day.attractions)
    parts = [
        f"当天安排：{stop_text}",
        f"共{len(day.attractions)}个景点，预计游览{visit_minutes}分钟",
    ]

    route_minutes = sum(route.duration_min or 0 for route in day.routes)
    if route_minutes:
        parts.append(f"景点间预计移动{route_minutes}分钟")
    if any(route.duration_min is None for route in day.routes):
        parts.append("部分路线时间待确认")
    return "，".join(parts) + "。"


def synchronize_plan_overviews(plan: TravelPlan, *, days: set[int] | None = None) -> TravelPlan:
    """Synchronize itinerary and city-plan descriptions with final attractions."""

    target_days = days
    itinerary = [
        day.model_copy(update={"description": build_day_overview(day)})
        if target_days is None or day.day in target_days
        else day
        for day in plan.itinerary
    ]
    itinerary_by_city_day = {
        (day.city, day.city_day): day
        for day in itinerary
        if target_days is None or day.day in target_days
    }

    city_plans = []
    for city_plan in plan.city_plans:
        days_for_city = []
        for city_day in city_plan.days:
            itinerary_day = itinerary_by_city_day.get((city_plan.city, city_day.day))
            if itinerary_day is None:
                days_for_city.append(city_day)
                continue
            days_for_city.append(
                DayPlan(
                    day=city_day.day,
                    attractions=[stop.name for stop in itinerary_day.attractions],
                    description=itinerary_day.description,
                )
            )
        city_plans.append(city_plan.model_copy(update={"days": days_for_city}))

    return plan.model_copy(update={"itinerary": itinerary, "city_plans": city_plans})
