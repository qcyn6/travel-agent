import asyncio
import json
import re

import httpx
from pydantic import ValidationError

from backend.models.experiment import BaselinePlan, ExperimentCase


class BaselinePlanningError(RuntimeError):
    pass


class DeepSeekBaselinePlanner:
    def __init__(self, api_key, *, base_url, model, timeout_seconds=45.0, max_retries=2, client=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def plan(self, case: ExperimentCase) -> BaselinePlan:
        if not self.api_key:
            raise BaselinePlanningError("未配置 LLM_API_KEY")
        prompt = (
            "不要调用搜索、地图、天气、知识库或任何工具，直接根据常识生成旅游方案。\n"
            f"需求：{case.text}\n共 {case.days} 天。"
            "只输出 JSON："
            '{"days":[{"day":1,"city":"城市名","attractions":'
            '[{"name":"景点名","estimated_visit_minutes":120}]}]}。'
            "days 必须从 1 连续编号，且严格等于要求天数。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是无工具旅游规划基线模型，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 1800,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise BaselinePlanningError(f"基线规划失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            plan = BaselinePlan.model_validate(json.loads(match.group(0) if match else content))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise BaselinePlanningError("基线规划结果格式异常") from exc
        if [day.day for day in plan.days] != list(range(1, case.days + 1)):
            raise BaselinePlanningError("基线规划天数或编号不正确")
        return plan

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries):
            try:
                if self.client is not None:
                    response = await self.client.post(
                        f"{self.base_url}/chat/completions", headers=headers, json=payload
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(
                            f"{self.base_url}/chat/completions", headers=headers, json=payload
                        )
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise BaselinePlanningError("基线规划网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise BaselinePlanningError("基线规划网络请求失败")
