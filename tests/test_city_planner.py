import httpx
import json
import pytest

from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.requirement import TravelRequirement
from backend.models.planning import PlanningCandidate
from backend.models.retrieval import AttractionDocument, HybridSearchResult
from backend.planner.city import CityPlanningError, DeepSeekCityPlanner, deterministic_city_plan
from backend.planner.service import CityPlanningService
from backend.schedule import ScheduleBuilder


def candidate(poi_id: str, name: str) -> AttractionDocument:
    return AttractionDocument(
        poi_id=poi_id,
        name=name,
        city="杭州市",
        category="景区",
        description=f"{name}的可靠景点描述，适合历史文化与摄影体验。",
        source_url="https://example.gov.cn/source",
    )


def requirement(days: int) -> TravelRequirement:
    return TravelRequirement(
        province="浙江省",
        cities=["杭州市"],
        days=days,
        interests=["历史文化", "摄影"],
        preference_text="希望轻松一点",
        travel_scope="single_city",
    )


@pytest.mark.asyncio
async def test_city_planner_accepts_exact_candidate_names_and_days():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "游玩时长" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"city":"杭州市","days":['
                            '{"day":1,"attractions":["西湖","雷峰塔"],'
                            '"description":"围绕西湖历史文化展开轻松的一日游览。"}]}'
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekCityPlanner(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
        ).plan(
            city_stay=CityStay(city="杭州市", days=1),
            requirement=requirement(1),
            candidates=[
                candidate("A1", "西湖"),
                candidate("A2", "雷峰塔").model_copy(
                    update={"visit_duration_minutes": 180}
                ),
            ],
        )

    assert result.city == "杭州市"
    assert result.days[0].attractions == ["西湖", "雷峰塔"]


@pytest.mark.asyncio
async def test_city_planner_rejects_invented_attraction():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"city":"杭州市","days":['
                            '{"day":1,"attractions":["虚构景点"],'
                            '"description":"这是一段长度足够但使用虚构景点的行程说明。"}]}'
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CityPlanningError, match="候选范围外"):
            await DeepSeekCityPlanner(
                "test-key",
                base_url="https://deepseek.test",
                model="test-model",
                client=client,
            ).plan(
                city_stay=CityStay(city="杭州市", days=1),
                requirement=requirement(1),
                candidates=[candidate("A1", "西湖")],
            )


@pytest.mark.asyncio
async def test_city_planner_can_keep_raw_output_for_experiment_scoring():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"city":"杭州市","days":['
                            '{"day":1,"attractions":["模型注释后的西湖"],'
                            '"description":"保留原始模型输出供实验评估使用。"}]}'
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekCityPlanner(
            "test-key",
            base_url="https://deepseek.test",
            model="test-model",
            client=client,
            validate_candidates=False,
        ).plan(
            city_stay=CityStay(city="杭州市", days=1),
            requirement=requirement(1),
            candidates=[candidate("A1", "西湖")],
        )

    assert result.days[0].attractions == ["模型注释后的西湖"]


def test_same_deterministic_planner_supports_one_and_three_days():
    candidates = [candidate(f"A{i}", name) for i, name in enumerate(["西湖", "雷峰塔", "良渚", "西溪", "宋城"], 1)]

    one_day = deterministic_city_plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
        candidates=candidates,
    )
    three_days = deterministic_city_plan(
        city_stay=CityStay(city="杭州市", days=3),
        requirement=requirement(3),
        candidates=candidates,
    )

    assert len(one_day.days) == 1
    assert len(three_days.days) == 3
    assert len({name for day in three_days.days for name in day.attractions}) == 5


@pytest.mark.asyncio
async def test_city_planning_service_falls_back_when_llm_fails():
    attraction = HybridSearchResult(
        **candidate("A1", "西湖").model_dump(),
        rrf_score=0.1,
        final_rank=1,
    )

    class Retrieval:
        async def search(self, **kwargs):
            return [attraction]

    class Planner:
        async def plan(self, **kwargs):
            raise CityPlanningError("temporary failure")

    result = await CityPlanningService(
        retrieval_service=Retrieval(), planner=Planner()
    ).plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
    )

    assert result.city_plan.days[0].attractions == ["西湖"]


@pytest.mark.asyncio
async def test_city_planning_service_keeps_catalog_tags_for_downstream_planning():
    attraction = HybridSearchResult(
        **candidate("A1", "西湖")
        .model_copy(update={"interest_tags": ["自然", "摄影"]})
        .model_dump(),
        tag_match_count=2,
        preference_similarity=0.8,
        keyword_score=0.6,
        preference_score=0.74,
        rrf_score=0.1,
        final_rank=1,
    )

    class Retrieval:
        async def search(self, **kwargs):
            return [attraction]

    class Planner:
        async def plan(self, **kwargs):
            planning_candidate = kwargs["candidates"][0]
            assert isinstance(planning_candidate, PlanningCandidate)
            assert planning_candidate.interest_tags == ["自然", "摄影"]
            assert planning_candidate.tag_match_count == 2
            assert planning_candidate.semantic_score == pytest.approx(0.8)
            assert planning_candidate.keyword_score == pytest.approx(0.6)
            assert planning_candidate.preference_score == pytest.approx(0.74)
            return deterministic_city_plan(
                city_stay=kwargs["city_stay"],
                requirement=kwargs["requirement"],
                candidates=kwargs["candidates"],
            )

    result = await CityPlanningService(
        retrieval_service=Retrieval(), planner=Planner()
    ).plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
    )

    assert result.city_plan.days[0].attractions == ["西湖"]


@pytest.mark.asyncio
async def test_route_aware_city_planner_applies_enriched_opening_hours_before_selection():
    candidates = [
        HybridSearchResult(
            **candidate("A1", "偏好最高但闭馆").model_dump(),
            preference_score=0.95,
            rrf_score=0.9,
            final_rank=1,
        ),
        HybridSearchResult(
            **candidate("A2", "偏好次高且开放").model_dump(),
            preference_score=0.85,
            rrf_score=0.8,
            final_rank=2,
        ),
    ]

    class OpeningHours:
        async def enrich_opening_hours(self, attractions):
            return [
                item.model_copy(
                    update={
                        "opening_hours_weekly": (
                            "周一至周日:闭馆"
                            if item.poi_id == "A1"
                            else "周一至周日:08:00-17:00"
                        ),
                        "opening_hours_status": "available",
                    }
                )
                for item in attractions
            ]

    class RouteService:
        async def resolve_segment(self, *args, **kwargs):
            pytest.fail("首站选择不需要查询景点间路线")

    class Planner:
        async def plan(self, **kwargs):
            return deterministic_city_plan(
                city_stay=kwargs["city_stay"],
                requirement=kwargs["requirement"],
                candidates=kwargs["candidates"],
            )

    result = await CityPlanningService(
        retrieval_service=object(),
        planner=Planner(),
        route_enrichment_service=RouteService(),
        schedule_builder=ScheduleBuilder(meal_minutes=0, buffer_minutes=0),
        opening_hours_service=OpeningHours(),
    ).plan_with_routes(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
        city_adcode="330100",
        attractions=candidates,
        narrate=False,
    )

    assert result.city_result.city_plan.days[0].attractions == ["偏好次高且开放"]
    assert result.city_result.attractions[1].opening_hours_status == "available"


@pytest.mark.asyncio
async def test_city_planning_service_keeps_scheduler_order_when_llm_reorders_candidates():
    first = HybridSearchResult(
        **candidate("A1", "首站高偏好")
        .model_copy(update={"longitude": 120.0, "latitude": 30.0})
        .model_dump(),
        preference_similarity=0.9,
        keyword_score=0.9,
        preference_score=0.9,
        rrf_score=0.9,
        final_rank=1,
    )
    near = HybridSearchResult(
        **candidate("A2", "近处次高偏好")
        .model_copy(update={"longitude": 120.01, "latitude": 30.0})
        .model_dump(),
        preference_similarity=0.8,
        keyword_score=0.8,
        preference_score=0.8,
        rrf_score=0.8,
        final_rank=2,
    )
    far = HybridSearchResult(
        **candidate("A3", "远处高偏好")
        .model_copy(update={"longitude": 120.3, "latitude": 30.0})
        .model_dump(),
        preference_similarity=0.82,
        keyword_score=0.82,
        preference_score=0.82,
        rrf_score=0.7,
        final_rank=3,
    )

    class Retrieval:
        async def search(self, **kwargs):
            return [first, near, far]

    class Planner:
        async def plan(self, **kwargs):
            # This deliberately proposes a different order. The optional
            # narrator must not regain ownership of attraction selection.
            return CityPlan(
                city="杭州市",
                days=[
                    DayPlan(
                        day=1,
                        attractions=["远处高偏好", "首站高偏好"],
                        description="这是由语言模型生成的主题说明，不负责决定景点顺序。",
                    )
                ],
            )

    result = await CityPlanningService(
        retrieval_service=Retrieval(), planner=Planner()
    ).plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
    )

    assert result.city_plan.days[0].attractions[:2] == ["首站高偏好", "近处次高偏好"]
    assert result.city_plan.days[0].description.startswith("这是由语言模型")


@pytest.mark.asyncio
async def test_city_planning_service_reports_empty_candidate_pool_as_planning_error():
    class Retrieval:
        async def search(self, **kwargs):
            return []

    class Planner:
        async def plan(self, **kwargs):
            pytest.fail("LLM planner should not run without local candidates")

    with pytest.raises(CityPlanningError, match="没有可用于规划的景点知识"):
        await CityPlanningService(
            retrieval_service=Retrieval(), planner=Planner()
        ).plan(
            city_stay=CityStay(city="杭州市", days=1),
            requirement=requirement(1),
        )


@pytest.mark.asyncio
async def test_city_planning_service_deduplicates_retrieval_and_plan_attractions():
    first = HybridSearchResult(
        **candidate("A1", "西湖").model_dump(),
        rrf_score=0.9,
        final_rank=1,
    )
    same_name_different_id = HybridSearchResult(
        **candidate("A2", "西湖").model_dump(),
        rrf_score=0.8,
        final_rank=2,
    )
    second = HybridSearchResult(
        **candidate("A3", "灵隐寺").model_dump(),
        rrf_score=0.7,
        final_rank=3,
    )

    class Retrieval:
        async def search(self, **kwargs):
            return [first, same_name_different_id, second]

    class Planner:
        async def plan(self, **kwargs):
            assert [item.name for item in kwargs["candidates"]] == ["西湖", "灵隐寺"]
            return CityPlan(
                city="杭州市",
                days=[
                    DayPlan(
                        day=1,
                        attractions=["西湖", "西湖", "灵隐寺"],
                        description="这是一个包含重复候选的测试行程说明。",
                    )
                ],
            )

    result = await CityPlanningService(
        retrieval_service=Retrieval(), planner=Planner()
    ).plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=requirement(1),
    )

    assert [item.name for item in result.attractions] == ["西湖", "灵隐寺"]
    assert result.city_plan.days[0].attractions == ["西湖", "灵隐寺"]
