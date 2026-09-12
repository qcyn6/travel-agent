import asyncio
import json
import re

import httpx
from pydantic import ValidationError

from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.models.requirement import TravelRequirement
from backend.models.retrieval import AttractionDocument


class CityPlanningError(RuntimeError):
    pass


class DeepSeekCityPlanner:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def plan(
        self,
        *,
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: list[AttractionDocument],
    ) -> CityPlan:
        if not self.api_key:
            raise CityPlanningError("未配置 LLM_API_KEY")
        candidate_text = "\n".join(
            f"- {item.name}（{item.category or '未知分类'}）：{item.description}"
            for item in candidates
        )
        prompt = (
            f"请为 {city_stay.city} 生成 {city_stay.days} 天游览计划。\n"
            f"用户兴趣：{'、'.join(requirement.interests) or '未指定'}\n"
            f"体验偏好：{requirement.preference_text or '未指定'}\n\n"
            f"只能使用以下候选景点，不得编造名称：\n{candidate_text}\n\n"
            "要求：days 数组必须严格覆盖 Day 1 到最后一天；景点尽量不重复；"
            "每天安排数量合理；description 简要说明当天主题和安排逻辑。"
            "只输出 JSON，格式："
            '{"city":"杭州市","days":[{"day":1,"attractions":["景点名"],'
            '"description":"当天说明"}]}。'
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是严谨的城市旅游行程规划器。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 1800,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise CityPlanningError(f"DeepSeek 城市规划请求失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            plan = CityPlan.model_validate(json.loads(match.group(0) if match else content))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise CityPlanningError("DeepSeek 城市规划结果格式异常") from exc
        self._validate_plan(plan, city_stay, candidates)
        return plan

    @staticmethod
    def _validate_plan(
        plan: CityPlan,
        city_stay: CityStay,
        candidates: list[AttractionDocument],
    ) -> None:
        if plan.city != city_stay.city or len(plan.days) != city_stay.days:
            raise CityPlanningError("城市规划结果的城市或天数不正确")
        allowed = {candidate.name for candidate in candidates}
        used = [name for day in plan.days for name in day.attractions]
        if not used or any(name not in allowed for name in used):
            raise CityPlanningError("城市规划使用了候选范围外的景点")

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                if self.client is not None:
                    response = await self.client.post(url, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise CityPlanningError("DeepSeek 城市规划网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise CityPlanningError("DeepSeek 城市规划网络请求失败")


def deterministic_city_plan(
    *,
    city_stay: CityStay,
    requirement: TravelRequirement,
    candidates: list[AttractionDocument],
) -> CityPlan:
    if not candidates:
        raise CityPlanningError(f"{city_stay.city} 没有可用于规划的景点知识")
    assignments: list[list[str]] = [[] for _ in range(city_stay.days)]
    for index, candidate in enumerate(candidates):
        assignments[index % city_stay.days].append(candidate.name)
    for index, names in enumerate(assignments):
        if not names:
            names.append(candidates[index % len(candidates)].name)
    preference = "、".join(requirement.interests) or "综合体验"
    return CityPlan(
        city=city_stay.city,
        days=[
            DayPlan(
                day=index + 1,
                attractions=names,
                description=f"围绕{preference}安排当天游览，兼顾节奏与景点体验。",
            )
            for index, names in enumerate(assignments)
        ],
    )
