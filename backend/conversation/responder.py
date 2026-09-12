import asyncio

import httpx

from backend.models.conversation import RequirementDraft


class ConversationResponseError(RuntimeError):
    pass


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
    ) -> str:
        if not self.api_key:
            raise ConversationResponseError("未配置 LLM_API_KEY")
        prompt = (
            "你是自然、友好且简洁的 AI 旅游规划师。先回应用户当前表达；"
            "不要机械地罗列字段，不要重复询问已知信息。每轮最多重点询问一个最重要的缺失信息。"
            "如果核心需求已经完整，引导用户查看并确认右侧旅行需求卡。"
            "目的地介绍最多两句话，只能使用提供的目的地资料，不编造事实。"
            "不要规划具体景点和逐日行程。\n\n"
            f"当前用户消息：{user_message}\n"
            f"当前需求：{requirement.model_dump_json()}\n"
            f"仍缺失的核心信息：{missing_fields}\n"
            f"目的地资料：{destination_context or '暂无'}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你负责通过自然对话逐步澄清旅游需求。"},
                *recent_messages[-8:],
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.5,
            "max_tokens": 500,
            "stream": False,
        }
        response = await self._request(payload)
        if response.status_code != 200:
            raise ConversationResponseError(f"DeepSeek 对话生成失败，HTTP {response.status_code}")
        try:
            text = response.json()["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ConversationResponseError("DeepSeek 对话响应格式异常") from exc
        if not text:
            raise ConversationResponseError("DeepSeek 返回了空回复")
        return text

    async def _request(self, payload):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries):
            try:
                if self.client is not None:
                    response = await self.client.post(url, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise ConversationResponseError("DeepSeek 对话网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise ConversationResponseError("DeepSeek 对话网络请求失败")
