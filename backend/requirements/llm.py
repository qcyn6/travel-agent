import asyncio
import re

import httpx
from pydantic import ValidationError

from backend.models.requirement import TravelRequirement


class RequirementLLMError(RuntimeError):
    pass


class DeepSeekRequirementParser:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def parse(self, text: str) -> TravelRequirement:
        if not text.strip():
            raise ValueError("旅游需求不能为空")
        if not self.api_key:
            raise RequirementLLMError("未配置 LLM_API_KEY")
        prompt = (
            "将用户旅游需求严格提取为 JSON。字段只能是：province、cities、days、interests、"
            "preference_text、travel_scope、start_date、transport_mode。province 为省级名称或 null；cities 为用户明确指定的城市列表；"
            "days 为正整数或 null；interests 为兴趣词列表；preference_text 保留节奏、体验等自由偏好，"
            "没有则为 null；travel_scope 只能是 single_city、multi_city、auto。"
            "start_date 使用 YYYY-MM-DD 或 null；transport_mode 只能是 auto、walking、transit、driving。"
            "明确一个城市时为 single_city，明确多个城市或想去两三个城市时为 multi_city，信息不足时为 auto。"
            "不得推荐城市、查询景点或规划行程，只输出 JSON。\n\n"
            f"用户输入：{text}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是旅游需求结构化解析器。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 700,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise RequirementLLMError(f"DeepSeek 需求解析失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            return TravelRequirement.model_validate_json(match.group(0) if match else content)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise RequirementLLMError("DeepSeek 需求解析结果格式异常") from exc

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
                    raise RequirementLLMError("DeepSeek 需求解析网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise RequirementLLMError("DeepSeek 需求解析网络请求失败")
