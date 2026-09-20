import asyncio
import json
import re

import httpx
from pydantic import ValidationError

from backend.attractions.dedupe import dedupe_attractions, normalize_attraction_name
from backend.models.city_stay import CityStay
from backend.models.itinerary import CityPlan, DayPlan
from backend.observability import observe_external_request
from backend.models.requirement import TravelRequirement
from backend.models.planning import PlanningCandidate
from backend.planner.dynamic_scheduler import dynamic_city_plan


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
        validate_candidates: bool = True,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client
        self.validate_candidates = bool(validate_candidates)

    async def plan(
        self,
        *,
        city_stay: CityStay,
        requirement: TravelRequirement,
        candidates: list[PlanningCandidate],
    ) -> CityPlan:
        if not self.api_key:
            raise CityPlanningError("未配置 LLM_API_KEY")
        candidates = dedupe_attractions(candidates)
        candidate_text = "\n".join(
            f"- {item.name}（{item.category or '未知分类'}）：{item.description}"
            f"；游玩时长：{item.visit_duration_minutes}分钟"
            for item in candidates
        )
        prompt = (
            f"请为 {city_stay.city} 生成 {city_stay.days} 天游览计划。\n"
            f"用户兴趣：{'、'.join(requirement.interests) or '未指定'}\n"
            f"体验偏好：{requirement.preference_text or '未指定'}\n\n"
            f"只能使用以下候选景点，不得编造名称：\n{candidate_text}\n\n"
            "要求：days 数组必须严格覆盖 Day 1 到最后一天；景点尽量不重复；"
            "候选景点已经按用户兴趣匹配度排序，优先使用前面的高匹配候选，"
            "不要为了凑数选择明显不符合兴趣的景点；每天安排数量合理；"
            "根据候选景点提供的游玩时长控制每天安排数量；"
            "description 简要说明当天主题和安排逻辑。"
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
        if self.validate_candidates:
            self._validate_plan(plan, city_stay, candidates)
        return plan

    @staticmethod
    def _validate_plan(
        plan: CityPlan,
        city_stay: CityStay,
        candidates: list[PlanningCandidate],
    ) -> None:
        if plan.city != city_stay.city or len(plan.days) != city_stay.days:
            raise CityPlanningError("城市规划结果的城市或天数不正确")
        allowed = {candidate.name for candidate in candidates}
        used = [name for day in plan.days for name in day.attractions]
        if not used or any(name not in allowed for name in used):
            raise CityPlanningError("城市规划使用了候选范围外的景点")
        normalized_names = [normalize_attraction_name(name) for name in used]
        if len(normalized_names) != len(set(normalized_names)):
            raise CityPlanningError("城市规划包含重复景点")

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "city_planning"):
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
    candidates: list[PlanningCandidate],
) -> CityPlan:
    try:
        return dynamic_city_plan(
            city_stay=city_stay,
            requirement=requirement,
            candidates=dedupe_attractions(candidates),
        ).city_plan
    except ValueError as exc:
        raise CityPlanningError(str(exc)) from exc


def deduplicate_city_plan(*, plan: CityPlan, candidates: list[PlanningCandidate]) -> CityPlan:
    """Repair repeated names while keeping every day usable.

    The model is asked not to repeat attractions, but the final boundary must
    not rely on that instruction alone. When an unused candidate is available,
    a later duplicate is replaced with it; if a city has fewer candidates than
    requested days, the original attraction is retained rather than producing
    an invalid empty day.
    """

    candidates = dedupe_attractions(candidates)
    candidate_by_name = {
        normalize_attraction_name(candidate.name): candidate
        for candidate in candidates
    }
    used_names: set[str] = set()
    repaired_days = []
    for day in plan.days:
        names: list[str] = []
        for name in day.attractions:
            normalized = normalize_attraction_name(name)
            if normalized and normalized in used_names:
                continue
            candidate = candidate_by_name.get(normalized)
            canonical_name = candidate.name if candidate is not None else name
            names.append(canonical_name)
            if normalized:
                used_names.add(normalized)

        if not names:
            replacement = next(
                (
                    candidate
                    for candidate in candidates
                    if normalize_attraction_name(candidate.name) not in used_names
                ),
                None,
            )
            if replacement is not None:
                names.append(replacement.name)
                used_names.add(normalize_attraction_name(replacement.name))
            else:
                # DayPlan requires at least one attraction. This only occurs
                # when the city catalog genuinely has fewer unique POIs than
                # the requested number of days.
                names.append(day.attractions[0])

        repaired_days.append(day.model_copy(update={"attractions": names}))
    return plan.model_copy(update={"days": repaired_days})
