import asyncio
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx


class WebContentError(RuntimeError):
    pass


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str):
        if not self._ignored_depth:
            text = " ".join(data.split())
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts)


class WebPageFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 12.0,
        max_characters: int = 12000,
        max_retries: int = 3,
        client=None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_characters = max_characters
        self.max_retries = max(1, max_retries)
        self.client = client

    async def fetch(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WebContentError("资料来源必须是有效的 HTTPS 地址")

        headers = {"User-Agent": "TravelAgentResearch/0.1 (+academic project)"}
        if self.client is not None:
            response = await self._get_with_retry(self.client, url, headers)
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await self._get_with_retry(client, url, headers)
        if response.status_code != 200:
            raise WebContentError(f"资料网页返回 HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            raise WebContentError("资料来源不是可解析的 HTML 页面")

        parser = _VisibleTextParser()
        parser.feed(response.text)
        content = parser.text()
        if len(content) < 80:
            raise WebContentError("资料网页正文过短，无法生成可靠摘要")
        return content[: self.max_characters]

    async def _get_with_retry(self, client, url: str, headers: dict[str, str]):
        for attempt in range(self.max_retries):
            try:
                response = await client.get(url, headers=headers, follow_redirects=True)
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise WebContentError("资料网页网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == self.max_retries:
                    return response
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise WebContentError("资料网页网络请求失败")
