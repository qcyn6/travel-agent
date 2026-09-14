import asyncio

import httpx

from backend.observability import observe_external_request

class SummarizationError(RuntimeError):
    pass


def fit_description(content: str, *, minimum: int = 100, maximum: int = 220) -> str:
    normalized = " ".join(content.strip().split())
    if len(normalized) < minimum:
        raise SummarizationError("DeepSeek 返回的景点描述过短")
    if len(normalized) <= maximum:
        return normalized

    candidate = normalized[:maximum]
    sentence_end = max(candidate.rfind(mark) for mark in "。！？")
    if sentence_end >= minimum - 1:
        return candidate[: sentence_end + 1]
    return candidate[: maximum - 1].rstrip("，；、 ") + "。"


class DeepSeekSummarizer:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise SummarizationError("未配置 LLM_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client

    async def summarize(self, *, name: str, city: str, category: str | None, source_text: str) -> str:
        prompt = (
            f"景点名称：{name}\n城市：{city}\n高德分类：{category or '未知'}\n\n"
            "请仅根据下方网页正文，生成一段适合旅游景点检索的中文描述。"
            "要求120至200字，包含核心特色、主要体验、适合的兴趣类型和游览特点；"
            "不要补充正文中没有的事实，不要写门票价格和临时开放时间，不要使用项目符号。\n\n"
            f"网页正文：\n{source_text}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是严谨的旅游资料编辑，只能基于给定来源总结。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 500,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.client is not None:
            response = await self._post_with_retry(self.client, headers, payload)
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await self._post_with_retry(client, headers, payload)
        if response.status_code != 200:
            raise SummarizationError(f"DeepSeek 摘要请求失败，HTTP {response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SummarizationError("DeepSeek 返回格式异常") from exc
        return fit_description(content)

    async def _post_with_retry(self, client, headers, payload):
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "knowledge_summary"):
                    response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise SummarizationError("DeepSeek 摘要网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == self.max_retries:
                    return response
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise SummarizationError("DeepSeek 摘要网络请求失败")
