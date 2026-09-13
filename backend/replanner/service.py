from backend.models.itinerary import DayPlan
from backend.models.replanning import DayReplanDecision, ReplanningResult
from backend.models.travel_plan import AttractionStop
from backend.replanner.deepseek import ReplannerError


class ReplanningService:
    def __init__(self, replanner, *, max_day_minutes: int = 600):
        self.replanner = replanner
        self.max_day_minutes = max_day_minutes

    async def replan(self, *, plan, evaluation, requirement, candidates_by_city):
        problem_days = sorted(
            {
                issue.day
                for issue in evaluation.issues
                if issue.severity == "high" and issue.day is not None
            }
        )
        changed_days = []
        used_fallback = False
        itinerary = list(plan.itinerary)
        for day_number in problem_days:
            index = next((i for i, item in enumerate(itinerary) if item.day == day_number), None)
            if index is None:
                continue
            day = itinerary[index]
            candidates = candidates_by_city.get(day.city, [])
            day_issues = [issue for issue in evaluation.issues if issue.day == day_number]
            try:
                decision = await self.replanner.replan_day(
                    day=day,
                    issues=day_issues,
                    candidates=candidates,
                    requirement=requirement,
                )
            except ReplannerError:
                decision = self._fallback_decision(day, day_issues)
                used_fallback = True
            updated = _apply_decision(day, decision, candidates)
            if [stop.poi_id for stop in updated.attractions] != [
                stop.poi_id for stop in day.attractions
            ]:
                itinerary[index] = updated
                changed_days.append(day_number)

        updated_plan = plan.model_copy(update={"itinerary": itinerary, "evaluation": None})
        updated_plan = _sync_city_plans(updated_plan, changed_days)
        return ReplanningResult(
            plan=updated_plan,
            changed_days=changed_days,
            used_fallback=used_fallback,
        )

    def _fallback_decision(self, day, issues) -> DayReplanDecision:
        stops = list(day.attractions)
        if len(stops) > 1:
            has_density_issue = any(issue.type == "schedule_density" for issue in issues)
            if has_density_issue:
                fixed_minutes = (
                    (day.schedule.meal_minutes + day.schedule.buffer_minutes)
                    if day.schedule is not None
                    else 150
                )
                selected = []
                used = fixed_minutes
                for stop in stops:
                    duration = stop.estimated_visit_minutes or 90
                    if selected and used + duration > self.max_day_minutes:
                        continue
                    selected.append(stop)
                    used += duration
                stops = selected or stops[:1]
            else:
                stops = stops[:-1]
        return DayReplanDecision(
            day=day.day,
            attraction_poi_ids=[stop.poi_id for stop in stops],
            description=f"根据行程检查结果调整第 {day.day} 天安排，减少明显冲突并保留核心体验。",
        )


def _apply_decision(day, decision, candidates):
    current = {stop.poi_id: stop for stop in day.attractions}
    candidate_lookup = {candidate.poi_id: candidate for candidate in candidates}
    stops = []
    for poi_id in decision.attraction_poi_ids:
        if poi_id in current:
            stops.append(current[poi_id].model_copy(update={"start_time": None, "end_time": None}))
            continue
        candidate = candidate_lookup[poi_id]
        stops.append(
            AttractionStop(
                poi_id=candidate.poi_id,
                name=candidate.name,
                city=candidate.city,
                category=candidate.category,
                description=candidate.description,
                longitude=candidate.longitude,
                latitude=candidate.latitude,
            )
        )
    return day.model_copy(
        update={
            "attractions": stops,
            "routes": [],
            "schedule": None,
            "description": decision.description,
        }
    )


def _sync_city_plans(plan, changed_days):
    if not changed_days:
        return plan
    day_lookup = {day.day: day for day in plan.itinerary}
    city_offsets = {}
    for itinerary_day in plan.itinerary:
        city_offsets.setdefault(itinerary_day.city, itinerary_day.day - itinerary_day.city_day)
    city_plans = []
    for city_plan in plan.city_plans:
        days = []
        offset = city_offsets[city_plan.city]
        for city_day in city_plan.days:
            global_day = offset + city_day.day
            if global_day in changed_days:
                itinerary_day = day_lookup[global_day]
                days.append(
                    DayPlan(
                        day=city_day.day,
                        attractions=[stop.name for stop in itinerary_day.attractions],
                        description=itinerary_day.description,
                    )
                )
            else:
                days.append(city_day)
        city_plans.append(city_plan.model_copy(update={"days": days}))
    return plan.model_copy(update={"city_plans": city_plans})
