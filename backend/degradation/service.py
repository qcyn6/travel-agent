from backend.models.degradation import DegradationNotice


def collect_degradation_notices(plan) -> list[DegradationNotice]:
    notices = []
    for day in plan.itinerary:
        # Historical snapshots may contain estimated routes. New planning
        # runs never create them; they are kept here only for compatibility.
        estimated_routes = sum(route.source == "haversine" for route in day.routes)
        unavailable_routes = sum(route.source == "unavailable" for route in day.routes)
        if estimated_routes:
            notices.append(
                DegradationNotice(
                    code="route_estimated",
                    level="info",
                    day=day.day,
                    message=(
                        f"第 {day.day} 天有 {estimated_routes} 段路线使用距离估算，"
                        "实际交通时间可能更长"
                    ),
                )
            )
        if unavailable_routes:
            notices.append(
                DegradationNotice(
                    code="route_unavailable",
                    level="warning",
                    day=day.day,
                    message=(
                        f"第 {day.day} 天有 {unavailable_routes} 段路线暂时无法获取，"
                        "相关交通时间未计入时间表，出发前请用地图确认"
                    ),
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
        if day.schedule is not None and day.schedule.status == "incomplete":
            notices.append(
                DegradationNotice(
                    code="schedule_incomplete",
                    level="warning",
                    day=day.day,
                    message=(
                        f"第 {day.day} 天时间表缺少部分路线耗时，"
                        "当天结束时间仅供参考"
                    ),
                )
            )
    for city in getattr(plan, "partial_knowledge_cities", []):
        notices.append(
            DegradationNotice(
                code="knowledge_partial",
                level="warning",
                day=None,
                message=f"{city}仅准备了部分景点知识，已使用现有资料生成方案，建议出发前补充核验",
            )
        )
    return notices
