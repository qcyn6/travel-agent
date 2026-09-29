"""LangChain agents used at safe, non-algorithmic planning boundaries."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from backend.attractions.dedupe import dedupe_attractions
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan
from backend.models.planning import PlanningCandidate
from backend.models.requirement import TravelRequirement
from backend.models.travel_plan import TravelPlan
from backend.models.weather import DailyWeatherAdvice
from backend.observability import observe_external_request
from backend.planner.city import CityPlanningError, DeepSeekCityPlanner


class LangChainCityNarrationAgent:
    """Generate city-day descriptions without owning attraction selection."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        validate_candidates: bool = True,
        agent: Any | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.validate_candidates = bool(validate_candidates)
        self.agent = agent or self._build_agent()

    def _build_agent(self):
        if not self.api_key:
            return None
        llm = ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=0.2,
            timeout=self.timeout_seconds,
            max_retries=0,
            max_tokens=1800,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return create_agent(
            model=llm,
            tools=[],
            system_prompt=(
                "你是严谨的城市旅游行程文案 Agent。只能依据给定候选景点生成每日说明，"
                "不得编造景点、城市、时间或路线事实。"
            ),
            response_format=CityPlan,
            name="city_narration_agent",
        )

    async def plan(
        self,
        *,
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: list[PlanningCandidate],
    ) -> CityPlan:
        if self.agent is None:
            raise CityPlanningError("未配置 LLM_API_KEY")
        candidates = dedupe_attractions(candidates)
        prompt = self._build_prompt(city_stay, requirement, candidates)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "city_planning_agent"):
                    result = await self.agent.ainvoke(
                        {"messages": [{"role": "user", "content": prompt}]}
                    )
                plan = result.get("structured_response") if isinstance(result, dict) else None
                if not isinstance(plan, CityPlan):
                    plan = CityPlan.model_validate(plan)
                if self.validate_candidates:
                    DeepSeekCityPlanner._validate_plan(plan, city_stay, candidates)
                return plan
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
        raise CityPlanningError("LangChain 城市规划 Agent 返回结果不可用") from last_error

    @staticmethod
    def _build_prompt(
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: list[PlanningCandidate],
    ) -> str:
        candidate_text = "\n".join(
            f"- {item.name}（{item.category or '未知分类'}）：{item.description}"
            f"；游玩时长：{item.visit_duration_minutes}分钟"
            for item in candidates
        )
        return (
            f"请为 {city_stay.city} 生成 {city_stay.days} 天游览计划说明。\n"
            f"用户兴趣：{'、'.join(requirement.interests) or '未指定'}\n"
            f"体验偏好：{requirement.preference_text or '未指定'}\n\n"
            f"只能使用以下候选景点，不得编造名称：\n{candidate_text}\n\n"
            "要求：days 数组必须严格覆盖 Day 1 到最后一天；景点尽量不重复；"
            "候选景点已经按用户兴趣匹配度排序，优先使用前面的高匹配候选；"
            "每天安排数量合理；description 简要说明当天主题和安排逻辑。"
        )


class DailyPlanDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    description: str = Field(min_length=10, max_length=400)


class AttractionDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=240)


class PlanReviewIssue(BaseModel):
    """A review hint attached to the itinerary day it affects."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    category: Literal["route", "schedule", "opening_hours", "other"] = "other"
    message: str = Field(min_length=1, max_length=500)


class PlanReviewResult(BaseModel):
    """Read-only review plus display-copy enhancement for a completed plan."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    repairable: bool = False
    issues: list[PlanReviewIssue] = Field(default_factory=list, max_length=10)
    summary: str = ""
    weather_advice: list[DailyWeatherAdvice] = Field(default_factory=list, max_length=7)
    day_descriptions: list[DailyPlanDescription] = Field(default_factory=list, max_length=7)
    attraction_descriptions: list[AttractionDescription] = Field(
        default_factory=list,
        max_length=100,
    )


class LangChainItineraryEnhancementAgent:
    """Review and enrich a completed plan without changing its itinerary.

    The deterministic evaluator remains the source of truth. This agent is a
    second opinion for explainability and operational monitoring. It may
    improve display-only prose and weather advice, but cannot reorder
    attractions or alter route and schedule data.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        agent: Any | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.agent = agent or self._build_agent()

    def _build_agent(self):
        if not self.api_key:
            return None
        llm = ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=0,
            timeout=self.timeout_seconds,
            max_retries=0,
            max_tokens=5000,
            # LangChain structured responses use tool_choice under the hood.
            # DeepSeek rejects tool_choice while reasoning mode is enabled.
            extra_body={"thinking": {"type": "disabled"}},
        )
        return create_agent(
            model=llm,
            tools=[],
            system_prompt=(
                "你是旅游行程复核与内容增强 Agent。行程选择、路线和时间表已经由确定性算法完成，"
                "你只能复核并润色展示文字，绝不能新增、删除、替换或重排景点，不能修改路线、"
                "交通时间、游览时间、酒店或用户时间窗口。"
                "请输出：1）只报告真实、具体、可解释的路线/时间窗口/营业时间问题；每条问题都必须带 day 整数，"
                "且 day 必须对应行程中实际存在的天数；按受影响的日期拆分问题，不得把多天问题合并成一条，"
                "不得省略 day 或输出全局问题；每条还必须标记 category（route、schedule、opening_hours、other）。"
                "确定性评估结果是权威依据：已被评估结果指出的路线时长、路线可靠性、时间窗口或已知营业时间问题，"
                "不要换一种说法重复输出；不要输出‘手动调整后的结果’、‘请按情况决定是否保留’等泛化提示。"
                "只报告规则评估未覆盖的新增事实，例如缺少营业时间数据；没有新增问题时 issues 留空。"
                "2）为每个行程日生成一条简洁自然的 daily description；"
                "3）为每个已安排景点按 poi_id 生成一条约30～80字的中文介绍；"
                "4）对每个 status=available 的天气日期生成一条实用 weather_advice。"
                "景点介绍只能依据输入中的景点名称、类别、本地简介、标签和游玩时长，不得编造历史、"
                "票价、开放状态或其他未经提供的事实。每日说明只能概括当天已有景点和行程主题。"
                "天气建议只能使用输入中的天气现象、温度、降水概率和风力，例如晴天防晒补水、"
                "多云灵活穿衣、降雨带伞防滑；不要臆造预警或闭园信息。"
                "不要评估兴趣覆盖率或整日行程密度。无可用天气预报的日期不得输出 weather_advice。"
                "景点介绍必须覆盖每个已安排景点且 poi_id 必须原样对应；每日说明和天气建议"
                "必须覆盖所有符合条件的日期。只返回结构化字段。"
            ),
            response_format=PlanReviewResult,
            name="itinerary_enhancement_review_agent",
        )

    async def review(
        self,
        *,
        plan: TravelPlan,
        requirement: TravelRequirement,
    ) -> PlanReviewResult:
        if self.agent is None:
            raise RuntimeError("未配置 LLM_API_KEY")
        plan_snapshot = self._plan_snapshot(plan)
        required_weather_days = {
            day.day
            for day in plan.itinerary
            if day.weather is not None and day.weather.status == "available"
        }
        required_days = {day.day for day in plan.itinerary}
        required_poi_ids = {
            stop.poi_id for day in plan.itinerary for stop in day.attractions
        }
        prompt = (
            "请复核并增强下面已完成的旅游行程。确定性评估结果是权威依据，不能推翻；"
            "issues 只填写确定性评估尚未覆盖的新增问题，不得复述其中已有的问题；"
            "也不要输出泛化的手动调整说明。你只能返回问题提示和展示用文字，不得改动行程数据。\n\n"
            f"用户需求：{requirement.model_dump_json(indent=2)}\n"
            f"确定性评估：{plan.evaluation.model_dump_json(indent=2) if plan.evaluation else '无'}\n"
            "输出必须覆盖所有行程日、所有已安排景点以及所有有可用天气的日期。"
            f"已完成行程（只包含最终选中的景点）：{json.dumps(plan_snapshot, ensure_ascii=False)}"
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "itinerary_enhancement_review_agent"):
                    result = await self.agent.ainvoke(
                        {"messages": [{"role": "user", "content": prompt}]}
                    )
                review = result.get("structured_response") if isinstance(result, dict) else None
                if not isinstance(review, PlanReviewResult):
                    review = PlanReviewResult.model_validate(review)
                missing_weather_days = required_weather_days - {
                    item.day for item in review.weather_advice
                }
                missing_days = required_days - {
                    item.day for item in review.day_descriptions
                }
                missing_poi_ids = required_poi_ids - {
                    item.poi_id for item in review.attraction_descriptions
                }
                invalid_issue_days = {
                    item.day for item in review.issues if item.day not in required_days
                }
                if (
                    attempt + 1 < self.max_retries
                    and (
                        missing_weather_days
                        or missing_days
                        or missing_poi_ids
                        or invalid_issue_days
                    )
                ):
                    missing_parts = []
                    if missing_weather_days:
                        missing_parts.append(f"天气建议日 {sorted(missing_weather_days)}")
                    if missing_days:
                        missing_parts.append(f"每日概览日 {sorted(missing_days)}")
                    if missing_poi_ids:
                        missing_parts.append(f"景点介绍 POI {sorted(missing_poi_ids)}")
                    if invalid_issue_days:
                        missing_parts.append(f"无效复核提示天数 {sorted(invalid_issue_days)}")
                    last_error = ValueError("结构化输出不完整：" + "；".join(missing_parts))
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                if invalid_issue_days:
                    review = review.model_copy(
                        update={
                            "issues": [
                                item for item in review.issues
                                if item.day in required_days
                            ]
                        }
                    )
                return review
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
        raise RuntimeError("LangChain 行程审核 Agent 返回结果不可用") from last_error

    @staticmethod
    def _plan_snapshot(plan: TravelPlan) -> dict[str, Any]:
        candidates_by_id = {
            candidate.poi_id: candidate
            for candidates in plan.replacement_candidates_by_city.values()
            for candidate in candidates
        }
        itinerary = []
        for day in plan.itinerary:
            attractions = []
            for stop in day.attractions:
                candidate = candidates_by_id.get(stop.poi_id)
                attractions.append(
                    {
                        "poi_id": stop.poi_id,
                        "name": stop.name,
                        "city": stop.city,
                        "category": stop.category,
                        "local_description": stop.description,
                        "interest_tags": candidate.interest_tags if candidate else [],
                        "visit_duration_minutes": stop.visit_duration_minutes,
                        "start_time": stop.start_time,
                        "end_time": stop.end_time,
                        "opening_hours": stop.opening_hours_weekly or stop.opening_hours_today,
                    }
                )
            itinerary.append(
                {
                    "day": day.day,
                    "city": day.city,
                    "city_day": day.city_day,
                    "date": day.date.isoformat() if day.date else None,
                    "description": day.description,
                    "attractions": attractions,
                    "routes": [
                        {
                            "origin": route.origin_name,
                            "destination": route.destination_name,
                            "mode": route.mode,
                            "duration_min": route.duration_min,
                            "estimated": route.estimated,
                            "source": route.source,
                        }
                        for route in day.routes
                    ],
                    "start_hotel_route": (
                        day.start_hotel_route.model_dump(mode="json")
                        if day.start_hotel_route is not None
                        else None
                    ),
                    "schedule": (
                        day.schedule.model_dump(mode="json")
                        if day.schedule is not None
                        else None
                    ),
                    "weather": (
                        day.weather.model_dump(mode="json")
                        if day.weather is not None
                        else None
                    ),
                }
            )
        return {"itinerary": itinerary}


# Backward-compatible name for callers that still refer to the review-only role.
LangChainPlanReviewAgent = LangChainItineraryEnhancementAgent


__all__ = [
    "AttractionDescription",
    "DailyPlanDescription",
    "LangChainCityNarrationAgent",
    "LangChainItineraryEnhancementAgent",
    "LangChainPlanReviewAgent",
    "PlanReviewIssue",
    "PlanReviewResult",
]
