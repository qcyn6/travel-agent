from datetime import date

import pytest

from backend.models.city_stay import CityStay
from backend.models.evaluation import EvaluationIssue, EvaluationResult
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.planning import PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan, TravelPlanResponse
from backend.models.weather import WeatherResult
from backend.planner.langchain_agents import (
    LangChainCityNarrationAgent,
    LangChainItineraryEnhancementAgent,
    LangChainPlanReviewAgent,
    PlanReviewResult,
)
from backend.planner.workflow import TravelPlanningWorkflow
from backend.requirements.langchain_agent import LangChainRequirementAgent


def _requirement() -> TravelRequirement:
    return TravelRequirement(
        province="浙江省",
        cities=["杭州市"],
        days=1,
        interests=["自然"],
        preference_text="轻松",
        travel_scope="single_city",
    )


def _candidate() -> PlanningCandidate:
    return PlanningCandidate(
        poi_id="A1",
        name="西湖",
        city="杭州市",
        category="风景名胜",
        description="适合慢慢游览的湖景景点。",
        source_url="https://example.com/west-lake",
    )


def test_deepseek_structured_agents_disable_thinking_mode(monkeypatch):
    import backend.planner.langchain_agents as planner_agents
    import backend.requirements.langchain_agent as requirement_agent

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def fake_create_agent(**kwargs):
        return kwargs

    monkeypatch.setattr(planner_agents, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(planner_agents, "create_agent", fake_create_agent)
    monkeypatch.setattr(requirement_agent, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(requirement_agent, "create_agent", fake_create_agent)

    itinerary = LangChainItineraryEnhancementAgent(
        "test-key", base_url="https://example.com", model="test-model"
    )
    requirement = LangChainRequirementAgent(
        "test-key", base_url="https://example.com", model="test-model"
    )

    assert itinerary.agent["model"].kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert requirement.agent["model"].kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.asyncio
async def test_review_failure_progress_identifies_fallback_reason():
    class FailingAgent:
        async def review(self, **kwargs):
            raise ValueError("provider rejected tool_choice")

    workflow = object.__new__(TravelPlanningWorkflow)
    workflow.plan_review_agent = FailingAgent()
    events = []

    async def progress(step, label, status):
        events.append((step, label, status))

    result = await workflow._review_plan(
        response=type("Response", (), {"plan": object(), "requirement": object()})(),
        progress=progress,
    )

    assert result["status"] == "unavailable"
    assert any(
        step == "plan_review" and "ValueError" in label
        for step, label, _ in events
    )


@pytest.mark.asyncio
async def test_langchain_narration_agent_returns_structured_plan_without_selecting_pois():
    class FakeAgent:
        async def ainvoke(self, payload):
            assert payload["messages"][0]["role"] == "user"
            return {
                "structured_response": CityPlan(
                    city="杭州市",
                    days=[DayPlan(day=1, attractions=["西湖"], description="围绕西湖安排轻松的一日游览。")],
                )
            }

    agent = LangChainCityNarrationAgent(
        None,
        base_url="https://example.com",
        model="test-model",
        agent=FakeAgent(),
    )
    result = await agent.plan(
        city_stay=CityStay(city="杭州市", days=1),
        requirement=_requirement(),
        candidates=[_candidate()],
    )

    assert result.days[0].attractions == ["西湖"]
    assert result.days[0].description.startswith("围绕")


@pytest.mark.asyncio
async def test_langchain_review_agent_is_read_only_and_returns_structured_review():
    class FakeAgent:
        async def ainvoke(self, payload):
            assert "行程" in payload["messages"][0]["content"]
            return {
                "structured_response": PlanReviewResult(
                    approved=True,
                    repairable=False,
                    summary="景点、城市和时间窗口一致。",
                    issues=[{"day": 1, "message": "景点之间的交通时间偏长，建议留意。"}],
                    weather_advice=[{"day": 1, "advice": "晴天适合户外游览，记得防晒补水。"}],
                    day_descriptions=[{
                        "day": 1,
                        "description": "上午沿湖散步，下午参观灵隐寺，行程留有休息时间。",
                    }],
                    attraction_descriptions=[{
                        "poi_id": "A1",
                        "description": "西湖湖面开阔，适合沿湖慢行并欣赏自然风景。",
                    }],
                )
            }

    plan = TravelPlan(
        province="浙江省",
        city_stays=[CityStay(city="杭州市", days=1)],
        city_plans=[
            CityPlan(
                city="杭州市",
                days=[
                    DayPlan(
                        day=1,
                        attractions=["西湖"],
                        description="围绕西湖安排轻松的一日游览。",
                    )
                ],
            )
        ],
        itinerary=[
            TravelDay(
                day=1,
                city="杭州市",
                city_adcode="330100",
                city_day=1,
                date=date(2026, 9, 20),
                attractions=[
                    AttractionStop(
                        poi_id="A1",
                        name="西湖",
                        city="杭州市",
                        description="适合慢慢游览的湖景景点。",
                    )
                ],
                description="围绕西湖安排轻松的一日游览。",
                weather=WeatherResult(
                    city="杭州市",
                    date=date(2026, 9, 20),
                    condition="晴",
                    temperature_min=22,
                    temperature_max=28,
                    precipitation_probability=0.1,
                    wind_level="2",
                    status="available",
                    source="qweather",
                ),
            )
        ],
    )
    result = await LangChainPlanReviewAgent(
        None,
        base_url="https://example.com",
        model="test-model",
        agent=FakeAgent(),
    ).review(plan=plan, requirement=_requirement())

    assert LangChainItineraryEnhancementAgent is LangChainPlanReviewAgent
    assert result.approved is True
    assert result.issues[0].day == 1
    assert result.weather_advice[0].day == 1
    assert "防晒" in result.weather_advice[0].advice
    assert result.day_descriptions[0].day == 1
    assert result.attraction_descriptions[0].poi_id == "A1"


@pytest.mark.asyncio
async def test_weather_advice_is_attached_without_changing_the_itinerary():
    requirement = _requirement()
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=date(2026, 9, 20),
        attractions=[AttractionStop(
            poi_id="A1",
            name="西湖",
            city="杭州市",
            description="本地原始景点简介。",
        )],
        weather=WeatherResult(
            city="杭州市",
            date=date(2026, 9, 20),
            condition="小雨",
            temperature_min=20,
            temperature_max=24,
            precipitation_probability=0.8,
            wind_level="2",
            status="available",
            source="qweather",
        ),
        description="围绕西湖安排轻松的一日游览。",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[CityStay(city="杭州市", days=1)],
        city_plans=[CityPlan(city="杭州市", days=[DayPlan(day=1, attractions=["西湖"], description="围绕西湖安排轻松的一日游览行程")])],
        itinerary=[day],
    )
    response = TravelPlanResponse(requirement=requirement, plan=plan)

    updated = await TravelPlanningWorkflow._repair_plan(
        object.__new__(TravelPlanningWorkflow),
        response=response,
        review={
            "approved": True,
            "weather_advice": [{"day": 1, "advice": "有雨，带伞并注意路面湿滑。"}],
            "day_descriptions": [{"day": 1, "description": "沿湖游览后前往灵隐寺，安排轻松并留出休息时间。"}],
            "attraction_descriptions": [{
                "poi_id": "A1",
                "description": "西湖湖面开阔，适合沿湖慢行并欣赏自然风景。",
            }],
        },
    )

    updated_day = updated.plan.itinerary[0]
    assert updated_day.weather.travel_advice == "有雨，带伞并注意路面湿滑。"
    assert updated_day.weather.travel_advice_source == "ai"
    assert updated_day.description == "沿湖游览后前往灵隐寺，安排轻松并留出休息时间。"
    assert updated_day.attractions[0].description == "西湖湖面开阔，适合沿湖慢行并欣赏自然风景。"
    assert updated.plan.city_plans[0].days[0].description == updated_day.description
    assert [stop.poi_id for stop in updated_day.attractions] == [stop.poi_id for stop in day.attractions]
    assert updated_day.routes == day.routes
    assert updated_day.schedule == day.schedule

    unavailable_day = day.model_copy(update={
        "weather": day.weather.model_copy(update={"status": "unavailable", "source": "none"})
    })
    unavailable_response = response.model_copy(update={
        "plan": plan.model_copy(update={"itinerary": [unavailable_day]})
    })
    unchanged = await TravelPlanningWorkflow._repair_plan(
        object.__new__(TravelPlanningWorkflow),
        response=unavailable_response,
        review={
            "approved": True,
            "weather_advice": [{"day": 1, "advice": "有雨，带伞并注意路面湿滑。"}],
        },
    )
    assert unchanged.plan.itinerary[0].weather.travel_advice is None


@pytest.mark.asyncio
async def test_weather_advice_has_grounded_fallback_when_reviewer_omits_it():
    requirement = _requirement()
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=date(2026, 9, 20),
        attractions=[AttractionStop(poi_id="A1", name="西湖", city="杭州市")],
        weather=WeatherResult(
            city="杭州市", date=date(2026, 9, 20), condition="多云",
            temperature_min=14, temperature_max=27, precipitation_probability=0.1,
            status="available", source="qweather",
        ),
        description="围绕西湖安排轻松的一日游览。",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[CityStay(city="杭州市", days=1)],
        city_plans=[CityPlan(city="杭州市", days=[DayPlan(day=1, attractions=["西湖"], description="A calm plan around West Lake.")])],
        itinerary=[day],
        evaluation=EvaluationResult(passed=True, issues=[]),
    )
    response = TravelPlanResponse(requirement=requirement, plan=plan)

    updated = await TravelPlanningWorkflow._repair_plan(
        object.__new__(TravelPlanningWorkflow),
        response=response,
        review={"approved": True, "weather_advice": []},
    )

    weather = updated.plan.itinerary[0].weather
    assert weather.travel_advice_source == "rule_based"
    assert "多云" in weather.travel_advice
    assert updated.plan.itinerary[0].attractions == day.attractions


@pytest.mark.asyncio
async def test_reviewer_issues_are_added_to_plan_evaluation():
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=date(2026, 9, 20),
        attractions=[AttractionStop(poi_id="A1", name="西湖", city="杭州市")],
        description="围绕西湖安排轻松的一日游览。",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[CityStay(city="杭州市", days=1)],
        city_plans=[CityPlan(city="杭州市", days=[DayPlan(day=1, attractions=["西湖"], description="A calm plan around West Lake.")])],
        itinerary=[day],
        evaluation=EvaluationResult(passed=True, issues=[]),
    )
    response = TravelPlanResponse(requirement=_requirement(), plan=plan)

    updated = await TravelPlanningWorkflow._repair_plan(
        object.__new__(TravelPlanningWorkflow),
        response=response,
        review={"approved": False, "issues": [{"day": 1, "message": "当天安排超出用户时间窗口"}]},
    )

    issue = updated.plan.evaluation.issues[0]
    assert issue.type == "review"
    assert issue.day == 1
    assert "超出用户时间窗口" in issue.message


@pytest.mark.asyncio
async def test_reviewer_skips_duplicate_route_and_generic_manual_adjustment_hints():
    day = TravelDay(
        day=1,
        city="杭州市",
        city_adcode="330100",
        city_day=1,
        date=date(2026, 9, 20),
        attractions=[
            AttractionStop(poi_id="A1", name="甲景点", city="杭州市"),
            AttractionStop(poi_id="A2", name="乙景点", city="杭州市"),
        ],
        description="当天安排两个景点。",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[CityStay(city="杭州市", days=1)],
        city_plans=[CityPlan(
            city="杭州市",
            days=[DayPlan(day=1, attractions=["甲景点", "乙景点"], description="今天按照自然主题安排两个景点游览。")],
        )],
        itinerary=[day],
        evaluation=EvaluationResult(
            passed=True,
            issues=[EvaluationIssue(
                type="route",
                severity="medium",
                day=1,
                message="甲景点到乙景点预计需要 66 分钟",
                suggestion="可考虑手动更换景点",
            )],
        ),
    )
    response = TravelPlanResponse(requirement=_requirement(), plan=plan)

    updated = await TravelPlanningWorkflow._repair_plan(
        object.__new__(TravelPlanningWorkflow),
        response=response,
        review={
            "approved": False,
            "issues": [
                {
                    "day": 1,
                    "category": "route",
                    "message": "甲景点到乙景点交通约66分钟，路线偏长。",
                },
                {
                    "day": 1,
                    "category": "other",
                    "message": "这是手动调整后的结果，确定性评估已提示按情况决定是否保留。",
                },
                {
                    "day": 1,
                    "category": "opening_hours",
                    "message": "甲景点暂未提供营业时间，建议出行前核实。",
                },
            ],
        },
    )

    issues = updated.plan.evaluation.issues
    assert len(issues) == 2
    assert issues[0].type == "route"
    assert issues[1].type == "review"
    assert "营业时间" in issues[1].message
    assert issues[1].suggestion is None
