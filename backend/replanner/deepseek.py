import asyncio
import json
import re

import httpx
from pydantic import ValidationError

from backend.models.replanning import DayReplanDecision
from backend.observability import observe_external_request


class ReplannerError(RuntimeError):
    pass


class DeepSeekDayReplanner:
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

    async def replan_day(self, *, day, issues, candidates, requirement) -> DayReplanDecision:
        if not self.api_key:
            raise ReplannerError("未配置 LLM_API_KEY")
        candidate_text = "\n".join(
            f"- poi_id={item.poi_id}；名称={item.name}；分类={item.category or '未知'}；"
            f"资料={item.description}"
            for item in candidates
        )
        issue_text = "\n".join(f"- {issue.message}；建议：{issue.suggestion or '无'}" for issue in issues)
        current_text = "、".join(
            f"{stop.name}({stop.poi_id}, {stop.estimated_visit_minutes or '未知'}分钟)"
            for stop in day.attractions
        )
        prompt = (
            f"只修改第 {day.day} 天（{day.city}）行程，其他日期绝对不能修改。\n"
            f"用户兴趣：{'、'.join(requirement.interests) or '未指定'}\n"
            f"用户偏好：{requirement.preference_text or '未指定'}\n"
            f"当前景点：{current_text}\n问题：\n{issue_text}\n\n"
            f"只能从以下候选景点选择 POI ID，不得编造：\n{candidate_text}\n\n"
            "可以删除、替换或调整顺序；优先解决高等级问题并尽量保留用户偏好。"
            "只输出 JSON："
            '{"day":1,"attraction_poi_ids":["POI_ID"],"description":"修改后的当天说明"}'
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是局部行程修正器，只能修改指定日期并使用给定 POI。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": 1000,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise ReplannerError(f"DeepSeek 局部重规划失败，HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            decision = DayReplanDecision.model_validate(
                json.loads(match.group(0) if match else content)
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise ReplannerError("DeepSeek 局部重规划结果格式异常") from exc
        allowed = {candidate.poi_id for candidate in candidates}
        if decision.day != day.day:
            raise ReplannerError("局部重规划修改了错误的日期")
        if len(set(decision.attraction_poi_ids)) != len(decision.attraction_poi_ids):
            raise ReplannerError("局部重规划结果包含重复景点")
        if any(poi_id not in allowed for poi_id in decision.attraction_poi_ids):
            raise ReplannerError("局部重规划使用了候选范围外的景点")
        return decision

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "day_replanning"):
                    if self.client is not None:
                        response = await self.client.post(url, headers=headers, json=payload)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise ReplannerError("DeepSeek 局部重规划网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise ReplannerError("DeepSeek 局部重规划网络请求失败")
