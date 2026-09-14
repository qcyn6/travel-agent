import asyncio
import json
import re

import httpx

from backend.models.retrieval import AttractionDocument
from backend.observability import observe_external_request


class RerankingError(RuntimeError):
    pass


class DeepSeekReranker:
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

    async def rerank(self, *, query: str, candidates: list[AttractionDocument]) -> list[str]:
        if not candidates:
            return []
        if not self.api_key:
            raise RerankingError("未配置 LLM_API_KEY")
        candidate_text = "\n".join(
            f"POI_ID={item.poi_id}\n名称={item.name}\n分类={item.category or '未知'}\n描述={item.description}"
            for item in candidates
        )
        prompt = (
            f"用户完整偏好：{query}\n\n候选景点：\n{candidate_text}\n\n"
            "请按与用户兴趣、体验偏好匹配程度重新排序。只能使用给出的 POI_ID，"
            "不得增加或遗漏，输出 JSON 数组，例如 [\"A1\",\"A2\"]，不要输出其他文字。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是旅游景点相关性重排器。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 300,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise RerankingError(f"DeepSeek 重排请求失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\[[\s\S]*\]", content)
            ordered = json.loads(match.group(0) if match else content)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise RerankingError("DeepSeek 重排结果格式异常") from exc
        expected = {candidate.poi_id for candidate in candidates}
        if not isinstance(ordered, list) or set(ordered) != expected or len(ordered) != len(expected):
            raise RerankingError("DeepSeek 重排结果包含未知或缺失的 POI")
        return ordered

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "reranking"):
                    if self.client is not None:
                        response = await self.client.post(url, headers=headers, json=payload)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise RerankingError("DeepSeek 重排网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise RerankingError("DeepSeek 重排网络请求失败")
