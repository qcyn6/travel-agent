from datetime import date
from types import SimpleNamespace

import pytest

from backend.evaluator import PlanEvaluator
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult
from backend.models.route import RouteSegment
from backend.models.travel_plan import (
    AttractionReplacementRequest,
    AttractionStop,
    TravelDay,
    TravelPlan,
    TravelPlanResponse,
)
from backend.planner.attraction_replacement import (
    build_replacement_preview,
    list_replacement_options,
)
from backend.schedule import ScheduleBuilder


def _candidate(poi_id, name, longitude, latitude, final_rank):
    return HybridSearchResult(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        longitude=longitude,
        latitude=latitude,
        description=f"{name}景点介绍",
        preference_score=0.8,
        final_rank=final_rank,
    )


def _response():
    requirement = TravelRequirement(
        province="浙江省",
        cities=["杭州市"],
        days=1,
        interests=["自然"],
        travel_scope="single_city",
        daily_start_time="09:00",
        daily_end_time="10:30",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[{"city": "杭州市", "days": 1}],
        city_plans=[CityPlan(city="杭州市", days=[
            DayPlan(day=1, attractions=["西湖", "灵隐寺"], description="围绕杭州的自然风光安排一天游览"),
        ])],
        itinerary=[TravelDay(
            day=1,
            city="杭州市",
            city_adcode="330100",
            city_day=1,
            date=date(2026, 10, 1),
            attractions=[
                AttractionStop(
                    poi_id="A",
                    name="西湖",
                    city="杭州市",
                    longitude=120.0,
                    latitude=30.0,
                    visit_duration_minutes=60,
                ),
                AttractionStop(
                    poi_id="B",
                    name="灵隐寺",
                    city="杭州市",
                    longitude=120.1,
                    latitude=30.1,
                    visit_duration_minutes=60,
                ),
            ],
            description="围绕杭州的自然风光安排一天游览",
        )],
        replacement_candidates_by_city={
            "杭州市": [_candidate("C", "西溪湿地", 120.2, 30.2, 1)],
        },
    )
    return TravelPlanResponse(requirement=requirement, plan=plan)


def test_replacement_order_uses_distance_from_each_position_previous_stop():
    response = _response()
    restored_plan = TravelPlan.model_validate(response.plan.model_dump(mode="json"))
    assert restored_plan.replacement_candidates_by_city["杭州市"][0].poi_id == "C"
    response.plan.itinerary[0].attractions.append(
        AttractionStop(
            poi_id="D",
            name="六和塔",
            city="杭州市",
            longitude=120.2,
            latitude=30.2,
        )
    )
    first_option = _candidate("X", "候选近西湖", 120.001, 30.001, 2)
    second_option = _candidate("Y", "候选近灵隐", 120.101, 30.101, 3)
    response.plan.replacement_candidates_by_city["杭州市"].extend(
        [first_option, second_option]
    )

    near_west_lake = list_replacement_options(
        response,
        day_number=1,
        current_poi_id="B",
    )
    near_lingyin = list_replacement_options(
        response,
        day_number=1,
        current_poi_id="D",
    )

    assert near_west_lake[0].candidate.poi_id == "X"
    assert near_lingyin[0].candidate.poi_id == "Y"


class FakeRouteService:
    async def enrich_day(self, day, *, requested_mode):
        routes = [
            RouteSegment(
                origin_poi_id=origin.poi_id,
                origin_name=origin.name,
                destination_poi_id=destination.poi_id,
                destination_name=destination.name,
                origin_longitude=origin.longitude,
                origin_latitude=origin.latitude,
                destination_longitude=destination.longitude,
                destination_latitude=destination.latitude,
                origin_city=day.city,
                destination_city=day.city,
                distance_m=15000,
                duration_min=70,
                mode="transit",
                source="amap_mcp",
                estimated=False,
            )
            for origin, destination in zip(day.attractions, day.attractions[1:])
        ]
        return day.model_copy(update={"routes": routes})


@pytest.mark.asyncio
async def test_manual_replacement_keeps_user_choice_and_returns_conflict_warnings():
    workflow = SimpleNamespace(
        attraction_photo_service=None,
        route_enrichment_service=FakeRouteService(),
        hotel_enrichment_service=None,
        schedule_builder=ScheduleBuilder(
            day_start_time="09:00",
            meal_minutes=0,
            buffer_minutes=0,
        ),
        evaluator=PlanEvaluator(),
    )

    preview = await build_replacement_preview(
        workflow,
        _response(),
        AttractionReplacementRequest(
            day=1,
            current_poi_id="B",
            replacement_poi_id="C",
        ),
    )

    updated_day = preview.result.plan.itinerary[0]
    assert [stop.poi_id for stop in updated_day.attractions] == ["A", "C"]
    assert updated_day.attractions[1].start_time == "11:10"
    assert updated_day.schedule.end_time == "12:40"
    assert any("超过你设置的10:30" in warning for warning in preview.warnings)
    assert sum("70 分钟" in warning for warning in preview.warnings) == 1
    manual_warnings = preview.result.plan.manual_adjustment_warnings.get(1, [])
    assert all("70" not in warning for warning in manual_warnings)
    assert any("附近酒店暂未匹配成功" in warning for warning in manual_warnings)
    route_issues = [
        issue for issue in preview.result.plan.evaluation.issues
        if issue.type == "route" and "70 分钟" in issue.message
    ]
    assert len(route_issues) == 1
