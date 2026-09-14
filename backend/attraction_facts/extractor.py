import asyncio
import re
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.attraction_facts.defaults import category_defaults
from backend.models.attraction_facts import AttractionFacts, OpeningHours
from backend.observability import observe_external_request


class FactsExtractionError(RuntimeError):
    pass


class _ExtractedFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: str = "unknown"
    estimated_visit_minutes: int | None = Field(default=None, ge=30, le=720)
    opening_hours: OpeningHours = Field(default_factory=OpeningHours)
    confidence: str = "unknown"


class DeepSeekAttractionFactsExtractor:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client=None,
        now_provider=datetime.now,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = client
        self.now_provider = now_provider

    async def extract(
        self,
        *,
        poi_id: str,
        name: str,
        city: str,
        category: str | None,
        source_url: str,
        source_text: str,
    ) -> AttractionFacts:
        if not self.api_key:
            raise FactsExtractionError("未配置 LLM_API_KEY")
        prompt = (
            "仅根据网页正文提取景点事实并输出 JSON，不得猜测。字段结构："
            '{"environment":"indoor|outdoor|mixed|unknown",'
            '"estimated_visit_minutes":整数或null,'
            '"opening_hours":{"status":"known|unknown","periods":['
            '{"weekdays":[1到7，周一为1],"open_time":"HH:MM",'
            '"close_time":"HH:MM","last_entry_time":"HH:MM或null"}],'
            '"raw_text":"来源中的开放时间原文或null",'
            '"applicability_text":"季节、日期、闭馆日等适用条件或null",'
            '"confidence":"high|medium|low|unknown","source_url":null,"checked_at":null},'
            '"confidence":"high|medium|low|unknown"}。'
            "只有正文明确给出常规开放时间时 status 才能为 known；无法可靠结构化、仅有临时通知或信息冲突时为 unknown。"
            "建议游玩时长只在正文有明确依据时填写，否则为 null。环境无法判断时填 unknown。\n\n"
            f"景点：{city}{name}\n分类：{category or '未知'}\n网页正文：\n{source_text}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是严谨的旅游事实抽取器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 1200,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise FactsExtractionError(f"DeepSeek 景点事实提取失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            extracted = _ExtractedFacts.model_validate_json(match.group(0) if match else content)
            if extracted.environment not in {"indoor", "outdoor", "mixed", "unknown"}:
                raise ValueError("environment 非法")
            if extracted.confidence not in {"high", "medium", "low", "unknown"}:
                raise ValueError("confidence 非法")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise FactsExtractionError("DeepSeek 景点事实提取结果格式异常") from exc
        checked_at = self.now_provider()
        fallback_duration, fallback_environment = category_defaults(category, name)
        duration = extracted.estimated_visit_minutes or fallback_duration
        duration_source = (
            "web_extracted" if extracted.estimated_visit_minutes is not None else "category_fallback"
        )
        environment = (
            extracted.environment if extracted.environment != "unknown" else fallback_environment
        )
        opening_hours = extracted.opening_hours.model_copy(
            update={"source_url": source_url, "checked_at": checked_at}
        )
        return AttractionFacts(
            poi_id=poi_id,
            environment=environment,
            estimated_visit_minutes=duration,
            duration_source=duration_source,
            opening_hours=opening_hours,
            source_url=source_url,
            confidence=extracted.confidence,
            checked_at=checked_at,
        )

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "facts_extraction"):
                    if self.client is not None:
                        response = await self.client.post(url, headers=headers, json=payload)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise FactsExtractionError("DeepSeek 景点事实提取网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise FactsExtractionError("DeepSeek 景点事实提取网络请求失败")
