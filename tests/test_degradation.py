from backend.degradation import collect_degradation_notices
from backend.models.route import RouteSegment
from backend.models.schedule import DaySchedule
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan
from backend.models.weather import WeatherResult


def test_degradation_notices_explain_partial_external_data():
    first = AttractionStop(poi_id="A1", name="甲", city="杭州市")
    second = AttractionStop(poi_id="A2", name="乙", city="杭州市")
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        attractions=[first, second],
        routes=[RouteSegment(
            origin_poi_id="A1", origin_name="甲", destination_poi_id="A2", destination_name="乙",
            mode="walking", source="unavailable",
        )],
        weather=WeatherResult(city="杭州市", status="unavailable", source="none"),
        schedule=DaySchedule(
            start_time="09:00", end_time="14:00", visit_minutes=180, route_minutes=0,
            meal_minutes=90, buffer_minutes=60, total_minutes=330, status="incomplete",
        ),
        description="用于测试统一降级说明的当天行程。",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[{"city": "杭州市", "days": 1}],
        city_plans=[{"city": "杭州市", "days": [{
            "day": 1, "attractions": ["甲", "乙"], "description": "用于测试统一降级说明的当天行程。",
        }]}],
        itinerary=[day],
        partial_knowledge_cities=["杭州市"],
    )

    notices = collect_degradation_notices(plan)

    assert {notice.code for notice in notices} == {
        "route_unavailable",
        "weather_unavailable",
        "knowledge_partial",
        "schedule_incomplete",
    }
    messages = {notice.code: notice.message for notice in notices}
    assert "相关交通时间未计入时间表" in messages["route_unavailable"]
    assert "当天结束时间仅供参考" in messages["schedule_incomplete"]
