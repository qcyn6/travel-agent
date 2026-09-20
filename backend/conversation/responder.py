import json
from collections.abc import AsyncIterator

import httpx

from backend.models.conversation import RequirementDraft
from backend.observability import observe_external_request


class ConversationResponseError(RuntimeError):
    pass


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
            else "当前是规划模式。你只能围绕省份/城市、天数、兴趣、出发日期、每日游玩时间和城市安排方式采集需求并推进规划；不要回答闲聊或目的地知识问题。"
        )
        prompt = (
            "你是自然、友好且简洁的 AI 旅行助手，名字叫“山”。普通模式和规划模式互斥，不能跨模式完成任务。"
            "规划模式会一步步帮用户确定目的地和出行条件；如果用户还没想好省份，可以明确让你结合兴趣和偏好推荐一个省份。景点资料来自项目数据库。"
            "不要说国内国外都可以，也不要承诺国外目的地的规划能力。"
            "先回应用户刚刚表达的内容，体现你听懂了；不要机械罗列字段，不要重复询问已知信息。"
            "如果刚刚补充或修改了旅行信息，先用一句话确认变化，再自然衔接。"
            "介绍目的地时，优先使用提供的省份/城市知识库资料；没有资料时坦诚说明，不要编造具体事实。"
            "知识库只用于稳定的目的地概览、旅行主题和地方体验，不要把它当作实时天气、开放时间、票价或路线结果。"
            "普通模式绝不主动介绍规划能力；用户问到规划时，只提示切换到规划模式，不邀请用户继续提供规划信息。"
            "当对话模式为 planning 时，所有字段都是生成计划的必要条件：省份或城市、天数、兴趣或体验偏好、出发日期、每天游玩的开始和结束时间、城市安排方式。"
            "规划边界必须说清楚：单城市总行程只能是1～3天；同省多城市总行程只能是1～7天，最多3个城市；"
            "如果安排2个城市至少需要2天，安排3个城市至少需要3天。超过7天不能承诺生成。"
            "当总天数为1～3天且城市安排方式还未确定时，先说明更适合一个城市深度游；如果用户想游多个城市，建议把天数增加到4天以上。"
            "1～3天时只能按一个城市安排；如果用户想游多个城市，必须先把天数增加到4天以上。4～7天时单城市不允许，必须按同省多城市处理，不要把单城市作为选项。"
            "如果用户还没有指定省份，必须先询问是否有想去的省份；用户明确让 AI 根据兴趣和偏好推荐省份后，这一步才算确定。进入省份后，如果单城市或同省多城市还没有具体城市，也必须询问有没有想去的城市；用户明确说没有、让 AI 推荐城市后，这一步才算确定。"
            "严格以‘仍缺失的必须信息’为准：只要列表中还有具体城市或城市推荐方式，就绝不能说‘所有条件都齐了’，也不能引导确认生成计划。"
            "出发日期至少从明天开始，不能接受今天或过去的日期。"
            "每天游玩时间必须明确开始和结束时间，例如‘每天早上8点到晚上9点’；结束时间必须晚于开始时间，不允许跨日。"
            "不要询问用户如何到达目的地，也不要询问景点之间怎么移动；景点间路线由地图工具根据实际位置自动推荐。"
            "只要其中任何一项没有明确，就必须继续追问，不能说‘之后补上即可’，不能把系统默认推荐当作用户已确认。"
            "每轮只重点询问一个最重要的缺失信息；只有全部信息明确后，才能引导用户回复‘确认生成计划’，不能提前总结为可生成。"
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
                        "普通模式只回答普通问题、目的地知识和旅行灵感，不收集或修改旅行需求，也不生成旅行计划。"
                        "普通模式下如果用户想制定具体行程，只能提示“请切换到规划模式”；不得邀请用户继续提供目的地、天数或偏好，"
                        "不得说“告诉我我来安排”，也不得承诺当前普通对话会生成行程。"
                        "规划模式只收集完整出行条件并生成行程，不处理闲聊或目的地知识问题。"
                        "规划范围是中国境内已经收录的省份和城市；如果用户没有想去的省份，可以让 AI 根据兴趣和偏好推荐，"
                        "单城市只支持1～3天；同省多城市只支持1～7天且最多3个城市，2个城市至少2天、3个城市至少3天；"
                        "总天数为1～3天时只能安排一个城市；想游多个城市则必须增加到4天以上；4～7天禁止单城市，必须按同省多城市处理，不要给出单城市选项；"
                        "出发日期至少从明天开始，不能接受今天或过去的日期；"
                        "绝不能把国外目的地说成当前可规划范围。省份未确定时，必须询问用户是否有想去的省份；用户明确让 AI 推荐省份后，这项才算确定。进入省份后，单城市或同省多城市如果还没有具体城市，必须询问用户有没有想去的城市；用户明确说没有并让 AI 推荐后，这项才算确定。planning 模式只有在省份/城市、天数、兴趣或体验偏好、出发日期、每日开始和结束时间、城市安排方式、具体城市或推荐方式全部明确时，才可以引导用户回复“确认生成计划”；任何一项缺失都必须继续追问。"
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
