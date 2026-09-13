from backend.models.degradation import DegradationNotice


def collect_degradation_notices(plan) -> list[DegradationNotice]:
    notices = []
    for day in plan.itinerary:
        estimated_routes = sum(route.source == "haversine" for route in day.routes)
        unavailable_routes = sum(route.source == "unavailable" for route in day.routes)
        if estimated_routes:
            notices.append(
                DegradationNotice(
                    code="route_estimated",
                    level="info",
                    day=day.day,
                    message=f"第 {day.day} 天有 {estimated_routes} 段路线使用直线距离估算",
                )
            )
        if unavailable_routes:
            notices.append(
                DegradationNotice(
                    code="route_unavailable",
                    level="warning",
                    day=day.day,
                    message=f"第 {day.day} 天有 {unavailable_routes} 段路线暂时无法获取",
                )
            )
        if day.weather is not None and day.weather.status == "skipped":
            notices.append(
                DegradationNotice(
                    code="weather_skipped",
                    level="info",
                    day=day.day,
                    message=f"第 {day.day} 天未提供日期，天气检查已跳过",
                )
            )
        elif day.weather is not None and day.weather.status == "unavailable":
            notices.append(
                DegradationNotice(
                    code="weather_unavailable",
                    level="warning",
                    day=day.day,
                    message=f"第 {day.day} 天天气预报暂不可用，请在出发前确认",
                )
            )
        unknown_openings = sum(
            stop.opening_hours is None or stop.opening_hours.status == "unknown"
            for stop in day.attractions
        )
        if unknown_openings:
            notices.append(
                DegradationNotice(
                    code="opening_hours_unknown",
                    level="info",
                    day=day.day,
                    message=f"第 {day.day} 天有 {unknown_openings} 个景点的开放时间待核验",
                )
            )
        if day.schedule is not None and day.schedule.status == "incomplete":
            notices.append(
                DegradationNotice(
                    code="schedule_incomplete",
                    level="warning",
                    day=day.day,
                    message=f"第 {day.day} 天时间表缺少部分路线耗时",
                )
            )
    if plan.replan_limit_reached:
        notices.append(
            DegradationNotice(
                code="replan_limit_reached",
                level="warning",
                message="自动优化已达到两次上限，仍存在的问题需要人工确认",
            )
        )
    return notices
