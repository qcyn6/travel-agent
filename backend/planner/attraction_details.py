"""LLM-generated attraction descriptions for the final planning response.

The local attraction table remains the source used for retrieval.  This
service runs only after the two retrieval layers have produced the bounded
candidate list, and rewrites the in-memory ``description`` field for the
current plan.  It deliberately does not add columns or write generated prose
back to the attraction table.
"""

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence

import httpx

from backend.models.requirement import TravelRequirement
from backend.models.retrieval import HybridSearchResult
from backend.observability import observe_external_request


logger = logging.getLogger(__name__)


class AttractionDescriptionGenerationError(RuntimeError):
    """Raised when the description response cannot be used safely."""


class DeepSeekAttractionDescriptionService:
    """Generate short, display-ready descriptions for local candidates."""

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
        self.max_retries = max(1, int(max_retries))
        self.client = client

    async def enrich(
        self,
        *,
        city: str,
        candidates: Sequence[HybridSearchResult],
        requirement: TravelRequirement | None = None,
    ) -> list[HybridSearchResult]:
        items = list(candidates)
        if not items or not self.api_key:
            return items

        payload = self._build_payload(city, items, requirement)
        try:
            response = await self._request(payload)
            if response.status_code != 200:
                raise AttractionDescriptionGenerationError(
                    f"DeepSeek 景点介绍生成失败，HTTP {response.status_code}"
                )
            descriptions = self._parse_response(response)
        except (AttractionDescriptionGenerationError, httpx.RequestError) as exc:
            # Generated prose is optional. A failed LLM call must never remove
            # a candidate that already passed the local retrieval rules.
            logger.warning(
                "AI 景点介绍补充失败，保留本地简介：city=%s error=%s",
                city,
                type(exc).__name__,
            )
            return items
        except Exception as exc:
            logger.warning(
                "AI 景点介绍响应异常，保留本地简介：city=%s error=%s",
                city,
                type(exc).__name__,
            )
            return items

        if not descriptions:
            return items
        return [
            item.model_copy(
                update={
                    "description": descriptions.get(
                        item.poi_id,
                        descriptions.get(_normalize_name(item.name), item.description),
                    )
                }
            )
            for item in items
        ]

    def _build_payload(
        self,
        city: str,
        candidates: Sequence[HybridSearchResult],
        requirement: TravelRequirement | None,
    ) -> dict:
        candidate_text = "\n".join(
            json.dumps(
                {
                    "poi_id": item.poi_id,
                    "name": item.name,
                    "category": item.category or "景点",
                    "tags": item.interest_tags,
                    "local_description": item.description,
                    "visit_duration_minutes": item.visit_duration_minutes,
                },
                ensure_ascii=False,
            )
            for item in candidates
        )
        interests = "、".join(requirement.interests) if requirement else ""
        preference = requirement.preference_text if requirement else ""
        prompt = (
            f"请为{city}候选景点生成用于旅行方案展示的简短介绍。\n"
            f"用户兴趣：{interests or '未指定'}\n"
            f"旅行偏好：{preference or '未指定'}\n\n"
            "以下景点已经通过本地两层筛选（兴趣标签硬筛和自由偏好检索），不能新增、删除或改名：\n"
            f"{candidate_text}\n\n"
            "要求：每个景点生成一条约30～80字的自然中文介绍，突出它适合的体验和看点；"
            "以给出的本地资料为基础，可以做常识性的概括，但不要编造票价、开放时间、实时状态、"
            "具体路线或无法从资料确认的历史细节。只输出 JSON，不要 Markdown，格式为："
            '{"attractions":[{"poi_id":"候选poi_id","name":"候选名称","description":"景点介绍"}]}。'
        )
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的旅行景点资料编辑，只能为给定候选生成展示用介绍。",
                },
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.3,
            "max_tokens": max(1600, min(3200, len(candidates) * 120)),
            "stream": False,
        }

    async def _request(self, payload: dict):
        if not self.api_key:
            raise AttractionDescriptionGenerationError("未配置 LLM_API_KEY")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "attraction_descriptions"):
                    if self.client is not None:
                        response = await self.client.post(url, headers=headers, json=payload)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError:
                if attempt + 1 == self.max_retries:
                    raise
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise AttractionDescriptionGenerationError("DeepSeek 景点介绍请求失败")

    @staticmethod
    def _parse_response(response) -> dict[str, str]:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AttractionDescriptionGenerationError("DeepSeek 景点介绍结果格式异常") from exc
        if not isinstance(content, str):
            raise AttractionDescriptionGenerationError("DeepSeek 景点介绍结果格式异常")
        match = re.search(r"\{[\s\S]*\}", content)
        try:
            data = json.loads(match.group(0) if match else content)
        except (TypeError, ValueError) as exc:
            raise AttractionDescriptionGenerationError("DeepSeek 景点介绍结果格式异常") from exc
        records = data.get("attractions") if isinstance(data, Mapping) else None
        if not isinstance(records, list):
            raise AttractionDescriptionGenerationError("DeepSeek 景点介绍结果格式异常")

        result: dict[str, str] = {}
        for record in records:
            if not isinstance(record, Mapping):
                continue
            description = record.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            description = " ".join(description.split()).strip()
            key = record.get("poi_id")
            if isinstance(key, str) and key.strip():
                result[key.strip()] = description
            name = record.get("name")
            if isinstance(name, str) and name.strip():
                result[_normalize_name(name)] = description
        return result


def build_attraction_description_service(settings):
    """Build the optional AI display-enrichment service from app settings."""

    if not getattr(settings, "llm_api_key", None):
        return None
    return DeepSeekAttractionDescriptionService(
        settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _normalize_name(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


__all__ = [
    "AttractionDescriptionGenerationError",
    "DeepSeekAttractionDescriptionService",
    "build_attraction_description_service",
]
