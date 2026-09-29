import asyncio
import re
from datetime import date

import httpx
from pydantic import ValidationError

from backend.models.requirement import TravelRequirement, validate_start_date
from backend.observability import observe_external_request


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

    async def parse(
        self,
        text: str,
        *,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> TravelRequirement:
        if not text.strip():
            raise ValueError("旅游需求不能为空")
        if not self.api_key:
            raise RequirementLLMError("未配置 LLM_API_KEY")
        history_context = self._format_conversation_history(conversation_history)
        prompt = (
            "将用户旅游需求严格提取为 JSON。字段只能是：province、cities、province_preference_confirmed、city_preference_confirmed、days、interests、"
            "preference_text、travel_scope、start_date、daily_start_time、daily_end_time、transport_mode。province 为省级名称或 null；cities 为用户明确指定的城市列表；"
            "days 为 1～7 的正整数或 null；1～3天只支持单城市，4～7天必须安排同省多城市（最多3个城市）；interests 为兴趣词列表；preference_text 保留节奏、体验等自由偏好，"
            "用户尚未表达、表达含糊或明确说没有额外偏好时均为 null；travel_scope 只能是 single_city、multi_city、auto。"
            "city_preference_confirmed 表示单城市或多城市安排下是否已经确认具体城市选择方式：用户说出城市名，或明确说没有指定城市、让 AI 推荐城市时为 true；否则为 false。"
            "province_preference_confirmed 表示用户没有指定省份、明确让 AI 根据兴趣和偏好推荐省份时为 true；用户明确说出省份时也为 true；否则为 false。"
            "不要把‘没有指定城市、你来推荐城市’理解成推荐省份，此时 city_preference_confirmed=true、province_preference_confirmed=false。"
            f"start_date 使用 YYYY-MM-DD 或 null；今天是 {date.today().isoformat()}，出发日期至少从明天开始；用户说“明天、后天、下周一”等相对日期时请尽量换算为具体日期；"
            "daily_start_time 和 daily_end_time 使用 HH:MM 或 null，表示每天景点游玩的开始和结束时间；不允许跨日，用户未明确时必须为 null；"
            "transport_mode 是系统内部字段，始终输出 auto；不要从用户如何到达目的地或任何景点移动描述中提取交通偏好，景点间路线由地图工具自动推荐。"
            "明确一个城市时为 single_city，明确多个城市或想去两三个城市时为 multi_city，信息不足时为 auto；不得替用户猜测缺失字段。"
            "未明确出发日期时必须为 null，未明确城市安排方式时必须为 auto。"
            "不得推荐城市、查询景点或规划行程，只输出 JSON。"
            "下面的历史对话是需求上下文：用户消息是事实来源，助手消息只能作为辅助上下文；如果二者冲突，以用户已经明确说过的内容为准。"
            "如果当前消息是在补充或修改历史需求，请输出合并后的完整需求，不能因为当前消息只提到一个新增城市就丢弃历史中已经确认的城市。\n\n"
            "<conversation_history>\n"
            f"{history_context}\n"
            "</conversation_history>\n\n"
            f"当前用户消息：{text}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是旅行需求分析 Agent。请阅读完整历史对话和当前用户消息，"
                        "提取并合并用户明确表达的完整旅行需求；用户消息优先于助手消息。"
                        "只输出结构化字段，不提问、不推荐景点、不规划行程。"
                    ),
                },
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
            result = TravelRequirement.model_validate_json(match.group(0) if match else content)
            validate_start_date(result.start_date)
            return result
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
            raise RequirementLLMError("DeepSeek 需求解析结果格式异常") from exc

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "requirement_parse"):
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

    @staticmethod
    def _format_conversation_history(
        conversation_history: list[dict[str, str]] | None,
    ) -> str:
        if not conversation_history:
            return "（暂无历史对话）"
        lines: list[str] = []
        for item in conversation_history:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "unknown")
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            role_name = {"user": "用户", "assistant": "助手"}.get(role, role)
            lines.append(f"{role_name}：{content}")
        return "\n".join(lines) or "（暂无历史对话）"
