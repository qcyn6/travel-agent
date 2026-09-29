"""User-directed attraction replacement using the original candidate pool."""

from __future__ import annotations

from backend.degradation import collect_degradation_notices
from backend.models.travel_plan import (
    AttractionReplacementOption,
    AttractionReplacementPreview,
    AttractionReplacementRequest,
    AttractionStop,
    TravelPlanResponse,
)
from backend.plan_overview import synchronize_plan_overviews
from backend.planner.dynamic_scheduler import (
    DISTANCE_WEIGHT,
    PREFERENCE_WEIGHT,
    calculate_distance_score,
    calculate_priority,
    haversine_distance_m,
)


def list_replacement_options(
    response: TravelPlanResponse,
    *,
    day_number: int,
    current_poi_id: str,
) -> list[AttractionReplacementOption]:
    """Rank unused candidates for this exact position in the itinerary."""

    plan = response.plan
    day_index = next(
        (index for index, day in enumerate(plan.itinerary) if day.day == day_number),
        None,
    )
    if day_index is None:
        return []
    day = plan.itinerary[day_index]
    stop_index = next(
        (
            index
            for index, stop in enumerate(day.attractions)
            if stop.poi_id == current_poi_id
        ),
        None,
    )
    if stop_index is None:
        return []

    used_elsewhere = {
        stop.poi_id
        for itinerary_day in plan.itinerary
        for stop in itinerary_day.attractions
        if not (
            itinerary_day.day == day_number and stop.poi_id == current_poi_id
        )
    }
    candidates = [
        item
        for item in plan.replacement_candidates_by_city.get(day.city, [])
        if item.poi_id != current_poi_id and item.poi_id not in used_elsewhere
    ]
    if not candidates:
        return []

    anchor = None
    if stop_index > 0:
        anchor = day.attractions[stop_index - 1]
    elif day_index > 0 and plan.itinerary[day_index - 1].city == day.city:
        previous_day = plan.itinerary[day_index - 1]
        if previous_day.attractions:
            anchor = previous_day.attractions[-1]

    distances = [
        haversine_distance_m(
            anchor.longitude if anchor is not None else None,
            anchor.latitude if anchor is not None else None,
            item.longitude,
            item.latitude,
        )
        if anchor is not None
        else None
        for item in candidates
    ]
    known_distances = [distance for distance in distances if distance is not None]
    max_distance = max(known_distances, default=None)
    options = []
    for candidate, distance in zip(candidates, distances, strict=True):
        distance_score = (
            calculate_distance_score(distance, max_distance)
            if anchor is not None
            else 0.0
        )
        priority = calculate_priority(
            candidate.preference_score,
            distance_score,
            preference_weight=PREFERENCE_WEIGHT,
            distance_weight=DISTANCE_WEIGHT,
        )
        options.append(
            AttractionReplacementOption(
                candidate=candidate,
                distance_m=distance,
                distance_score=distance_score,
                priority_score=priority,
            )
        )
    options.sort(
        key=lambda option: (
            -option.priority_score,
            -option.candidate.preference_score,
            -option.candidate.tag_match_count,
            option.distance_m if option.distance_m is not None else float("inf"),
            option.candidate.final_rank,
        )
    )
    return options


async def build_replacement_preview(
    workflow,
    response: TravelPlanResponse,
    request: AttractionReplacementRequest,
    *,
    include_display_enrichment: bool = True,
) -> AttractionReplacementPreview:
    """Apply a proposed swap in memory and return the resulting warnings."""

    plan = response.plan
    day_index = next(
        (index for index, day in enumerate(plan.itinerary) if day.day == request.day),
        None,
    )
    if day_index is None:
        raise ValueError("找不到要调整的行程日期")
    day = plan.itinerary[day_index]
    stop_index = next(
        (
            index
            for index, stop in enumerate(day.attractions)
            if stop.poi_id == request.current_poi_id
        ),
        None,
    )
    if stop_index is None:
        raise ValueError("当前景点已不在这一天的行程中")
    options = list_replacement_options(
        response,
        day_number=request.day,
        current_poi_id=request.current_poi_id,
    )
    selected_option = next(
        (
            option
            for option in options
            if option.candidate.poi_id == request.replacement_poi_id
        ),
        None,
    )
    if selected_option is None:
        raise ValueError("只能选择该城市中尚未安排的候选景点")

    candidate = selected_option.candidate
    description = candidate.description
    image_url = None
    if include_display_enrichment:
        enhancement_agent = getattr(workflow, "plan_review_agent", None)
        description_service = getattr(workflow, "attraction_description_service", None)
        # Keep the legacy prose service only as a compatibility fallback for
        # deployments that do not have the unified LangChain agent configured.
        if description_service is not None and enhancement_agent is None:
            try:
                enriched = await description_service.enrich(
                    city=day.city,
                    candidates=[candidate],
                    requirement=response.requirement,
                )
                if enriched:
                    description = enriched[0].description
            except Exception:
                pass
        photo_service = getattr(workflow, "attraction_photo_service", None)
        if photo_service is not None:
            try:
                images = await photo_service.enrich(city=day.city, candidates=[candidate])
                image_url = images.get(candidate.poi_id)
            except Exception:
                image_url = None
    replacement = AttractionStop(
        poi_id=candidate.poi_id,
        name=candidate.name,
        city=candidate.city,
        category=candidate.category,
        description=description,
        image_url=image_url,
        longitude=candidate.longitude,
        latitude=candidate.latitude,
        visit_duration_minutes=candidate.visit_duration_minutes,
        opening_hours_weekly=candidate.opening_hours_weekly,
        opening_hours_today=candidate.opening_hours_today,
        opening_hours_status=candidate.opening_hours_status,
    )
    stops = list(day.attractions)
    stops[stop_index] = replacement
    updated_day = day.model_copy(update={"attractions": stops})

    route_service = workflow.route_enrichment_service
    if route_service is not None:
        updated_day = await route_service.enrich_day(
            updated_day,
            requested_mode=response.requirement.transport_mode,
        )
        if stop_index == 0 and updated_day.start_hotel is not None:
            updated_day = updated_day.model_copy(
                update={
                    "start_hotel_route": await route_service.resolve_segment(
                        updated_day.start_hotel,
                        updated_day.attractions[0],
                        requested_mode=response.requirement.transport_mode,
                        city_adcode=updated_day.city_adcode,
                    )
                }
            )
    else:
        updated_day = updated_day.model_copy(
            update={
                "routes": [],
                **({"start_hotel_route": None} if stop_index == 0 else {}),
            }
        )

    hotel_changed = stop_index == len(day.attractions) - 1
    hotel_service = workflow.hotel_enrichment_service
    if hotel_changed:
        updated_day = updated_day.model_copy(update={"hotel": None, "hotel_route": None})
        enrich_day = getattr(hotel_service, "enrich_day", None)
        if callable(enrich_day):
            try:
                updated_day = await enrich_day(
                    updated_day,
                    requested_mode=response.requirement.transport_mode,
                )
            except Exception:
                # A hotel lookup failure must not discard the user's attraction choice.
                pass

    updated_day = workflow.schedule_builder.build_day_allowing_manual_conflicts(
        updated_day,
        day_start_time=response.requirement.daily_start_time,
        day_end_time=response.requirement.daily_end_time,
    )
    itinerary = list(plan.itinerary)
    itinerary[day_index] = updated_day
    affected_day_indices = [day_index]

    if (
        hotel_changed
        and day_index + 1 < len(itinerary)
        and itinerary[day_index + 1].city == updated_day.city
    ):
        next_day = itinerary[day_index + 1]
        next_day = next_day.model_copy(
            update={"start_hotel": updated_day.hotel, "start_hotel_route": None}
        )
        if updated_day.hotel is not None and route_service is not None and next_day.attractions:
            try:
                next_day = next_day.model_copy(
                    update={
                        "start_hotel_route": await route_service.resolve_segment(
                            updated_day.hotel,
                            next_day.attractions[0],
                            requested_mode=response.requirement.transport_mode,
                            city_adcode=next_day.city_adcode,
                        )
                    }
                )
            except Exception:
                pass
        next_day = workflow.schedule_builder.build_day_allowing_manual_conflicts(
            next_day,
            day_start_time=response.requirement.daily_start_time,
            day_end_time=response.requirement.daily_end_time,
        )
        itinerary[day_index + 1] = next_day
        affected_day_indices.append(day_index + 1)

    warnings_by_day = dict(plan.manual_adjustment_warnings)
    warnings_by_day.pop(request.day, None)
    if len(affected_day_indices) > 1:
        warnings_by_day.pop(itinerary[day_index + 1].day, None)
    for affected_index in affected_day_indices:
        affected_day = itinerary[affected_index]
        if affected_index == day_index and hotel_changed and affected_day.hotel is None:
            hotel_warning = "更换的是当天最后一个景点，但附近酒店暂未匹配成功，请自行确认住宿。"
            warnings_by_day[affected_day.day] = [hotel_warning]

    updated_plan = plan.model_copy(
        update={
            "itinerary": itinerary,
            "manual_adjustment_warnings": warnings_by_day,
        }
    )
    updated_plan = synchronize_plan_overviews(updated_plan)
    updated_plan = updated_plan.model_copy(
        update={
            "evaluation": workflow.evaluator.evaluate(updated_plan, response.requirement),
            "notices": collect_degradation_notices(updated_plan),
        }
    )
    updated_response = TravelPlanResponse(
        requirement=response.requirement,
        plan=updated_plan,
    )
    enhancement_agent = getattr(workflow, "plan_review_agent", None)
    if include_display_enrichment and enhancement_agent is not None:
        try:
            review = await enhancement_agent.review(
                plan=updated_response.plan,
                requirement=updated_response.requirement,
            )
            review_data = (
                review.model_dump(mode="json")
                if hasattr(review, "model_dump")
                else review
            )
            updated_response = await workflow._repair_plan(
                response=updated_response,
                review=review_data,
            )
        except Exception:
            # Copy enhancement remains optional; never undo the user's swap.
            pass
    affected_days = {itinerary[index].day for index in affected_day_indices}
    evaluation = updated_response.plan.evaluation
    preview_warnings = []
    seen_warnings = set()
    if evaluation is not None:
        for issue in evaluation.issues:
            if issue.day not in affected_days or issue.type not in {
                "route",
                "schedule",
                "manual_adjustment",
                "review",
            }:
                continue
            normalized = "".join(
                character
                for character in issue.message
                if not character.isspace() and character not in "，。；：、！？!?.,"
            )
            if normalized in seen_warnings:
                continue
            seen_warnings.add(normalized)
            preview_warnings.append(issue.message)
    return AttractionReplacementPreview(
        result=updated_response,
        warnings=preview_warnings,
    )
__all__ = ["build_replacement_preview", "list_replacement_options"]
