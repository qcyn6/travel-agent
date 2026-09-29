import json
import re
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from backend.models.conversation import RequirementDraft
from backend.observability import observe_external_request


class ConversationResponseError(RuntimeError):
    pass


class PlanningResponseDecision(BaseModel):
    """Structured action returned by the planning dialogue model."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["ask", "confirm", "generate"]
    next_field: str | None = None
    reply: str
    ready_for_confirmation: bool = False


_FIELD_LABELS = {
    "province": "省份",
    "cities": "城市",
    "days": "旅行天数",
    "interests": "兴趣偏好",
    "preference_text": "旅行风格",
    "travel_scope": "城市安排方式",
    "province_preference_confirmed": "省份推荐方式",
    "city_preference": "具体城市或城市推荐方式",
    "city_preference_confirmed": "具体城市或城市推荐方式",
    "start_date": "出发日期",
    "daily_start_time": "每日开始时间",
    "daily_end_time": "每日结束时间",
}


class DeepSeekConversationResponder:
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

    async def respond(
        self,
        *,
        user_message: str,
        requirement: RequirementDraft,
        missing_fields: list[str],
        destination_context: str | None,
        recent_messages: list[dict[str, str]],
        changed_fields: list[str] | None = None,
        conversation_mode: str = "planning",
    ) -> str:
        chunks: list[str] = []
        async for chunk in self.stream(
            user_message=user_message,
            requirement=requirement,
            missing_fields=missing_fields,
            destination_context=destination_context,
            recent_messages=recent_messages,
            changed_fields=changed_fields,
            conversation_mode=conversation_mode,
        ):
            chunks.append(chunk)
        text = "".join(chunks).strip()
        if not text:
            raise ConversationResponseError("DeepSeek 返回了空回复")
        return text

    async def decide_planning(
        self,
        *,
        user_message: str,
        requirement: RequirementDraft,
        missing_fields: list[str],
        destination_context: str | None,
        recent_messages: list[dict[str, str]],
        changed_fields: list[str] | None = None,
    ) -> PlanningResponseDecision:
        """Ask the model for a validated, structured planning-turn decision."""

        if not self.api_key:
            raise ConversationResponseError("未配置 LLM_API_KEY")
        changed_text = "、".join(
            _FIELD_LABELS.get(field, field) for field in (changed_fields or [])
        ) or "无"
        prompt = (
            "你是旅行规划对话控制器。请结合完整对话和当前需求，决定本轮如何推进。"
            "用户可以在一句话中同时提供多个条件；不要重复询问已经明确的内容。"
            "规划必须明确省份或授权推荐省份、城市或授权推荐城市、旅行天数、兴趣标签、"
            "自由填写的旅行节奏或体验偏好、出发日期、每日开始和结束时间。"
            "城市安排方式由系统根据天数自动决定：1～3天单城市，4～7天同省多城市，不能询问用户选择单城市或多城市。"
            "如果仍有缺失字段，每轮只选择一个最重要的字段询问；如果所有字段完整，先总结并引导用户确认生成计划。"
            "不允许编造数据库外的目的地，不允许在字段缺失时声称可以生成计划。"
            "只输出 JSON，不要 Markdown，不要额外解释。JSON 格式必须是："
            '{"action":"ask|confirm|generate",'
            '"next_field":"destination|days|preferences|preference_text|start_date|daily_time_window|city_preference|null",'
            '"reply":"给用户的自然语言回复",'
            '"ready_for_confirmation":true或false}。'
            "当仍有缺失字段时 action 必须为 ask；当全部完整但用户尚未明确确认时 action 为 confirm；"
            "只有用户明确表示确认生成时才使用 generate。"
            f"\n\n当前用户消息：{user_message}"
            f"\n当前需求：{requirement.model_dump_json()}"
            f"\n本轮新增或修改：{changed_text}"
            f"\n仍缺失字段：{missing_fields}"
            f"\n省市知识库资料：{destination_context or '暂无'}"
            f"\n最近对话：{json.dumps(recent_messages[-8:], ensure_ascii=False)}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严格遵守状态约束的旅行规划对话控制器。"
                        "必须返回合法 JSON；不能把 travel_scope 作为 next_field。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 700,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        try:
            async with observe_external_request("deepseek", "planning_decision"):
                if self.client is not None:
                    response = await self.client.post(url, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise ConversationResponseError("DeepSeek 规划决策网络请求失败") from exc
        if response.status_code != 200:
            raise ConversationResponseError(
                f"DeepSeek 规划决策请求失败，HTTP {response.status_code}"
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            data = json.loads(match.group(0) if match else content)
            decision = PlanningResponseDecision.model_validate(data)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ConversationResponseError("DeepSeek 规划决策结果格式异常") from exc
        if not decision.reply.strip():
            raise ConversationResponseError("DeepSeek 规划决策返回了空回复")
        return decision

    async def stream(
        self,
        *,
        user_message: str,
        requirement: RequirementDraft,
        missing_fields: list[str],
        destination_context: str | None,
        recent_messages: list[dict[str, str]],
        changed_fields: list[str] | None = None,
        conversation_mode: str = "planning",
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise ConversationResponseError("未配置 LLM_API_KEY")
        changed_text = "、".join(
            _FIELD_LABELS.get(field, field) for field in (changed_fields or [])
        ) or "无"
        mode_rules = (
            "当前是普通模式。你只能回答普通问题、目的地知识和旅行灵感；不要采集、修改或总结旅行需求，"
            "不要询问目的地、天数、偏好，不要生成或承诺生成旅行计划。"
            "如果用户表达想制定具体行程，只能简短说明“请切换到规划模式”；不要说“告诉我我来安排”，"
            "不要继续追问任何规划信息，也不要承诺当前普通对话会生成行程。"
            if conversation_mode == "general"
            else "当前是规划模式。你只能围绕省份/城市、天数、兴趣、自由偏好、出发日期和每日游玩时间采集需求并推进规划；不要回答闲聊或目的地知识问题。"
        )
        prompt = (
            "你是自然、友好且简洁的 AI 旅行助手，名字叫“山”。普通模式和规划模式互斥，不能跨模式完成任务。"
            "规划模式由你结合完整上下文主动引导用户，目标是准确提取必要需求，而不是机械地逐项填表。"
            "如果用户还没想好省份或城市，可以先了解旅行天数、兴趣和旅行风格，再说明将据此推荐目的地。景点资料来自项目数据库。"
            "不要说国内国外都可以，也不要承诺国外目的地的规划能力。"
            "先回应用户刚刚表达的内容，体现你听懂了；不要机械罗列字段，不要重复询问已知信息。"
            "如果刚刚补充或修改了旅行信息，先用一句话确认变化，再自然衔接。"
            "省份、城市、景点的基础介绍可以直接使用模型已有知识回答；如果提供了项目文档资料，则优先依据文档。"
            "路线、天气等实时信息必须以工具结果为准，不要用模型记忆替代；不要在回复中输出‘数据来源’、供应商名称或来源标签，直接自然地呈现结果即可。"
            "知识库只用于稳定的目的地概览、旅行主题和地方体验，不要把它当作实时天气、开放时间、票价或路线结果。"
            "普通模式绝不主动介绍规划能力；用户问到规划时，只提示切换到规划模式，不邀请用户继续提供规划信息。"
            "当对话模式为 planning 时，用户需要明确：省份或授权推荐省份、城市或授权推荐城市、天数、兴趣标签、自由填写的旅行节奏或体验偏好、出发日期、每天游玩的开始和结束时间。"
            "兴趣标签与自由偏好是两个独立字段，但用户可以在同一句话里同时表达，必须一次全部提取并确认，不能因为原本的提问顺序而重复询问。"
            "规划边界必须说清楚：单城市总行程只能是1～3天；同省多城市总行程只能是1～7天，最多3个城市；"
            "如果安排2个城市至少需要2天，安排3个城市至少需要3天。超过7天不能承诺生成。"
            "城市范围由系统根据天数自动确定，不能把单城市或多城市当作问题询问用户：1～3天自动按单城市深度游，4～7天自动按同省多城市。"
            "1～3天时只能按一个城市安排；如果用户想游多个城市，必须先把天数增加到4天以上。4～7天时单城市不允许，必须按同省多城市处理，不要把单城市作为选项。"
            "如果用户还没有指定省份，必须先询问是否有想去的省份；用户明确让 AI 根据兴趣和偏好推荐省份后，这一步才算确定。进入省份后，如果单城市或同省多城市还没有具体城市，也必须询问有没有想去的城市；用户明确说没有、让 AI 推荐城市后，这一步才算确定。"
            "严格以‘仍缺失的必须信息’为准。你要综合当前需求、刚刚变化的字段和缺失列表，选择此刻最有价值的一项继续问；不要求机械遵循固定字段顺序。"
            "如果用户不知道去哪里，先确认他是否授权你推荐，再围绕天数、六类兴趣标签和自由偏好继续了解；不要在信息不足时随意编造目的地。"
            "只要列表中还有具体城市或城市推荐方式，就绝不能说‘所有条件都齐了’，也不能引导确认生成计划。"
            "出发日期至少从明天开始，不能接受今天或过去的日期。"
            "每天游玩时间必须明确开始和结束时间，例如‘每天早上8点到晚上9点’；结束时间必须晚于开始时间，不允许跨日。"
            "不要询问用户如何到达目的地，也不要询问景点之间怎么移动；景点间路线由地图工具根据实际位置自动推荐。"
            "只要其中任何一项没有明确，就必须继续追问，不能说‘之后补上即可’，不能把系统默认推荐当作用户已确认。"
            "每轮只重点询问一个最重要的缺失信息，可以顺带确认本轮已经理解的多个条件。只有全部信息明确后，先自然总结完整旅行画像，再引导用户回复‘确认生成计划’，不能提前总结为可生成。"
            "不要提及表单、需求卡或任何页面布局。语气像熟悉旅行的朋友，句子短一些，像真实对话。\n\n"
            f"模式规则：{mode_rules}\n"
            f"对话模式：{conversation_mode}\n"
            f"当前用户消息：{user_message}\n"
            f"当前需求：{requirement.model_dump_json()}\n"
            f"本轮新增或修改的信息：{changed_text}\n"
            f"仍缺失的必须信息：{missing_fields}\n"
            f"省份/城市知识库资料：{destination_context or '暂无'}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是“山”，一个 AI 旅行助手。普通模式和规划模式互斥，不能跨模式完成任务。"
                        "普通模式只回答旅游相关的普通问题、目的地知识和旅行灵感，不收集或修改旅行需求，也不生成旅行计划。"
                        "普通模式下如果用户想制定具体行程，只能提示“请切换到规划模式”；不得邀请用户继续提供目的地、天数或偏好，"
                        "不得说“告诉我我来安排”，也不得承诺当前普通对话会生成行程。"
                        "规划模式只收集完整出行条件并生成行程，不处理闲聊或目的地知识问题。"
                        "规划范围是中国境内已经收录的省份和城市；如果用户没有想去的省份，可以让 AI 根据兴趣和偏好推荐，"
                        "单城市只支持1～3天；同省多城市只支持1～7天且最多3个城市，2个城市至少2天、3个城市至少3天；"
                        "总天数为1～3天时只能安排一个城市；想游多个城市则必须增加到4天以上；4～7天禁止单城市，必须按同省多城市处理，不要给出单城市选项；"
                        "出发日期至少从明天开始，不能接受今天或过去的日期；"
                        "绝不能把国外目的地说成当前可规划范围。省份未确定时，必须询问用户是否有想去的省份；用户明确让 AI 推荐省份后，这项才算确定。进入省份后，如果还没有具体城市，必须询问用户有没有想去的城市；用户明确说没有并让 AI 推荐后，这项才算确定。城市范围不询问用户，由天数自动决定：1～3天单城市，4～7天同省多城市。planning 模式只有在省份/城市、天数、兴趣标签、自由填写的旅行节奏或体验偏好、出发日期、每日开始和结束时间、具体城市或推荐方式全部明确时，才可以引导用户回复“确认生成计划”；任何一项缺失都必须继续追问。"
                        "景点间路线不需要用户指定，由地图工具根据景点位置自动推荐。"
                    ),
                },
                *recent_messages[-8:],
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.5,
            "max_tokens": 500,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        try:
            async with observe_external_request("deepseek", "conversation_stream"):
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code != 200:
                            raise ConversationResponseError(
                                f"DeepSeek 对话生成失败，HTTP {response.status_code}"
                            )
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                content = json.loads(data)["choices"][0]["delta"].get("content") or ""
                            except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                                raise ConversationResponseError("DeepSeek 流式响应格式异常") from exc
                            if content:
                                yield content
        except httpx.RequestError as exc:
            raise ConversationResponseError("DeepSeek 对话网络请求失败") from exc
