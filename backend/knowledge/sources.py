import asyncio
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx


class SourceNotFoundError(LookupError):
    pass


class SourceSearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveredSource:
    url: str
    extracted_text: str | None = None


class SourceDiscovery(Protocol):
    async def find_source(self, attraction_name: str, city: str) -> DiscoveredSource: ...


_CURATED_SOURCES = {
    "杭州西湖风景名胜区": "https://sjfw.mct.gov.cn/site/dataservice/culdetails?curId=10&id=34878",
    "西溪国家湿地公园": "https://xixishidi.cn/resume/",
    "良渚古城遗址公园": "https://www.lzsite.cn/",
    "杭州宋城": "https://www.songcn.com/park",
    "太白山国家森林公园": "https://www.eco.gov.cn/news_info/40438.html",
    "关山草原景区": "https://lyj.baoji.gov.cn/col1188/202509/t20250904_1205223.html",
}

_NAME_SUFFIXES = (
    "风景名胜区",
    "国家湿地公园",
    "国家森林公园",
    "旅游景区",
    "遗址公园",
    "博物馆",
    "景区",
    "公园",
)


class CuratedSourceDiscovery:
    async def find_source(self, attraction_name: str, city: str = "") -> DiscoveredSource:
        del city
        source_url = _CURATED_SOURCES.get(attraction_name)
        if source_url is None:
            raise SourceNotFoundError(f"尚未找到景点的可靠资料来源：{attraction_name}")
        return DiscoveredSource(url=source_url)


class TavilyGovernmentSourceDiscovery:
    """Search nationwide, accepting only relevant Chinese government pages."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.tavily.com",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        max_results: int = 8,
        client=None,
        cache=None,
        cache_ttl_seconds: int = 43200,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.max_results = max(1, min(max_results, 20))
        self.client = client
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds

    async def find_source(self, attraction_name: str, city: str) -> DiscoveredSource:
        if not self.api_key:
            raise SourceNotFoundError("未配置 WEB_SEARCH_API_KEY，无法搜索全国景点权威来源")

        cache_key = f"search:{city}:{attraction_name}"
        if self.cache is not None:
            cached = await self.cache.get_json(cache_key)
            if isinstance(cached, dict) and isinstance(cached.get("url"), str):
                return DiscoveredSource(
                    url=cached["url"],
                    extracted_text=cached.get("extracted_text"),
                )

        payload = {
            "query": f"{city} {attraction_name} 官方 文旅局 景区介绍",
            "topic": "general",
            "search_depth": "basic",
            "max_results": self.max_results,
            "include_domains": ["gov.cn"],
            "include_answer": False,
            "include_raw_content": "text",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.client is not None:
            response = await self._post_with_retry(self.client, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await self._post_with_retry(client, payload, headers)

        if response.status_code != 200:
            raise SourceSearchError(f"权威来源搜索服务返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceSearchError("权威来源搜索服务返回了无效数据") from exc
        if not isinstance(body, dict) or not isinstance(body.get("results", []), list):
            raise SourceSearchError("权威来源搜索服务返回了无效数据")
        results = body.get("results", [])

        aliases = _attraction_aliases(attraction_name)
        for result in results:
            if not isinstance(result, dict):
                continue
            url = result.get("url")
            title = result.get("title") or ""
            content = result.get("content") or ""
            if isinstance(url, str) and _is_approved_result(
                url=url,
                searchable_text=f"{title} {content}",
                aliases=aliases,
            ):
                raw_content = result.get("raw_content")
                extracted_text = raw_content if isinstance(raw_content, str) else content
                discovered = DiscoveredSource(
                    url=url,
                    extracted_text=extracted_text[:12000] if extracted_text else None,
                )
                if self.cache is not None:
                    await self.cache.set_json(
                        cache_key,
                        {"url": discovered.url, "extracted_text": discovered.extracted_text},
                        ttl_seconds=self.cache_ttl_seconds,
                    )
                return discovered
        raise SourceNotFoundError(f"没有搜索到可验证的政府或文旅来源：{attraction_name}")

    async def _post_with_retry(self, client, payload: dict, headers: dict):
        for attempt in range(self.max_retries):
            try:
                response = await client.post(
                    f"{self.base_url}/search",
                    json=payload,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                if attempt + 1 == self.max_retries:
                    raise SourceSearchError("权威来源搜索服务网络请求失败") from exc
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and (
                attempt + 1 < self.max_retries
            ):
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return response
        raise SourceSearchError("权威来源搜索服务网络请求失败")


class NationwideSourceDiscovery:
    def __init__(self, curated: SourceDiscovery, web_search: SourceDiscovery):
        self.curated = curated
        self.web_search = web_search

    async def find_source(self, attraction_name: str, city: str) -> DiscoveredSource:
        try:
            return await self.curated.find_source(attraction_name, city)
        except SourceNotFoundError:
            return await self.web_search.find_source(attraction_name, city)


def _attraction_aliases(name: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", name)
    aliases = {normalized}
    for suffix in _NAME_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
            aliases.add(normalized[: -len(suffix)])
    return tuple(sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True))


def _is_approved_result(*, url: str, searchable_text: str, aliases: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (hostname == "gov.cn" or hostname.endswith(".gov.cn")):
        return False
    normalized_text = re.sub(r"\s+", "", f"{unquote(url)} {searchable_text}")
    return any(alias in normalized_text for alias in aliases)
