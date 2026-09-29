"""LangChain requirement agent with the legacy parser contract."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from backend.models.requirement import TravelRequirement, validate_start_date
from backend.observability import observe_external_request
from backend.requirements.llm import RequirementLLMError


REQUIREMENT_AGENT_SYSTEM_PROMPT = (
    "你是旅行需求分析 Agent，负责从完整会话中提取并合并用户明确表达的旅行需求。"
    "系统会同时提供历史对话和当前用户消息；你必须阅读全部上下文，不要只分析当前一句话。"
    "用户消息是需求事实来源，助手消息只能作为辅助上下文；如果助手的提问、建议或总结与用户事实冲突，以用户明确表达为准。"
    "如果当前消息是在补充、修改或追加需求，请输出合并后的完整需求，不能丢弃历史中已经确认的城市、天数、偏好、日期或时间。"
    "只输出 TravelRequirement 对应的结构化字段，不输出自然语言回复，不自行提问，不推荐景点，不查询实时信息，不规划景点顺序、路线、酒店或完整行程。"
    "用户没有明确表达的字段必须保持为空、null 或 auto，不得根据常识猜测。"
    "完整性由下游系统根据结构化需求和硬性规则判断：信息不完整时下游会继续追问，信息完整时下游才会进入确认或规划流程。"
)


class LangChainRequirementAgent:
    """Extract requirements into the existing validated Pydantic model."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        agent: Any | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.agent = agent or self._build_agent()

    def _build_agent(self):
        if not self.api_key:
            return None
        llm = ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=0,
            timeout=self.timeout_seconds,
            max_retries=0,
            max_tokens=700,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return create_agent(
            model=llm,
            tools=[],
            system_prompt=REQUIREMENT_AGENT_SYSTEM_PROMPT,
            response_format=TravelRequirement,
            name="requirement_agent",
        )

    async def parse(
        self,
        text: str,
        *,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> TravelRequirement:
        if not text.strip():
            raise ValueError("旅游需求不能为空")
        if self.agent is None:
            raise RequirementLLMError("未配置 LLM_API_KEY")

        prompt = self._build_prompt(text, conversation_history=conversation_history)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with observe_external_request("deepseek", "requirement_agent"):
                    result = await self.agent.ainvoke(
                        {"messages": [{"role": "user", "content": prompt}]}
                    )
                parsed = result.get("structured_response") if isinstance(result, dict) else None
                if not isinstance(parsed, TravelRequirement):
                    parsed = TravelRequirement.model_validate(parsed)
                validate_start_date(parsed.start_date)
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
        raise RequirementLLMError("LangChain 需求 Agent 返回结果不可用") from last_error

    @staticmethod
    def _build_prompt(
        text: str,
        *,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> str:
        history_context = LangChainRequirementAgent._format_conversation_history(
            conversation_history
        )
        return (
            "根据完整历史对话和当前用户消息，提取并合并用户当前的完整旅行需求。"
            "将结果严格输出为结构化字段。province 为省级名称或 null；"
            "cities 为用户明确指定的城市列表；days 为 1～7 的正整数或 null；"
            "interests 为兴趣词列表；preference_text 保留节奏、体验等自由偏好；"
            "travel_scope 只能是 single_city、multi_city、auto；"
            "用户说出城市名或明确让 AI 推荐城市时 city_preference_confirmed=true；"
            "用户说出省份或明确让 AI 推荐省份时 province_preference_confirmed=true；"
            "不得替用户猜测缺失字段；缺失字段保持 null、空列表或 auto。start_date 使用 YYYY-MM-DD 或 null，"
            f"今天是 {date.today().isoformat()}，出发日期至少从明天开始；"
            "daily_start_time 和 daily_end_time 使用 HH:MM 或 null；"
            "transport_mode 始终输出 auto，不要从用户交通表达中提取交通偏好；"
            "1～3天只支持单城市，4～7天必须安排同省多城市。"
            "下面的历史对话是需求上下文：用户消息是事实来源，助手消息只能作为辅助上下文；如果二者冲突，以用户已经明确说过的内容为准。"
            "如果当前消息是在补充或修改历史需求，请输出合并后的完整需求，不能因为当前消息只提到一个新增城市就丢弃历史中已经确认的城市。\n\n"
            "<conversation_history>\n"
            f"{history_context}\n"
            "</conversation_history>\n\n"
            f"当前用户消息（用户输入）：{text}"
        )

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


__all__ = ["LangChainRequirementAgent"]
