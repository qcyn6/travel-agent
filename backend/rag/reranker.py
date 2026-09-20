import asyncio
import json
import re

import httpx

from backend.models.retrieval import AttractionDocument
from backend.observability import observe_external_request


class RerankingError(RuntimeError):
    pass


class DashScopeReranker:
    """Dedicated DashScope text reranker used after hybrid candidate recall."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str = "qwen3-rerank",
        instruct: str = "Retrieve semantically similar text.",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.instruct = instruct
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def rerank(self, *, query: str, candidates: list[AttractionDocument]) -> list[str]:
        if not candidates:
            return []
        if not self.api_key:
            raise RerankingError("未配置 RERANK_API_KEY 或 DASHSCOPE_API_KEY")

        payload = {
            "model": self.model,
            "query": query,
            "documents": [candidate.index_text() for candidate in candidates],
            # Hybrid retrieval usually returns fewer than 30 candidates. Asking
            # for all of them keeps the reranker contract complete and lets the
            # hard tag ordering in HybridRetrievalService run afterwards.
            "top_n": len(candidates),
            "instruct": self.instruct,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            detail = self._response_detail(response)
            suffix = f": {detail}" if detail else ""
            raise RerankingError(
                f"DashScope {self.model} 重排请求失败，HTTP {response.status_code}{suffix}"
            )

        try:
            results = response.json()["results"]
            if not isinstance(results, list):
                raise TypeError("results 不是数组")
            ranked = []
            for item in results:
                index = item["index"]
                score = float(item["relevance_score"])
                if isinstance(index, bool) or not isinstance(index, int):
                    raise TypeError("index 不是整数")
                if index < 0 or index >= len(candidates):
                    raise ValueError("index 超出候选范围")
                ranked.append((index, score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            indices = [index for index, _ in ranked]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise RerankingError("DashScope 重排结果格式异常") from exc

        expected = set(range(len(candidates)))
        if set(indices) != expected or len(indices) != len(expected):
            raise RerankingError("DashScope 重排结果包含未知或缺失的候选文档")
        return [candidates[index].poi_id for index in indices]

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/reranks"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("dashscope", "reranking"):
                    if self.client is not None:
                        response = await self.client.post(url, headers=headers, json=payload)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise RerankingError("DashScope 重排网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise RerankingError("DashScope 重排网络请求失败")

    @staticmethod
    def _response_detail(response) -> str:
        try:
            body = response.json()
        except (ValueError, TypeError):
            return response.text[:200].replace("\n", " ")
        if isinstance(body, dict):
            code = body.get("code")
            message = body.get("message")
            if code and message:
                return f"{code}: {message}"
            if message:
                return str(message)
        return ""


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
