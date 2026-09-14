from datetime import datetime

import httpx
import pytest

from backend.knowledge.service import KnowledgeEnrichmentService
from backend.knowledge.sources import (
    CuratedSourceDiscovery,
    DiscoveredSource,
    NationwideSourceDiscovery,
    SourceNotFoundError,
    TavilyGovernmentSourceDiscovery,
)
from backend.knowledge.summarizer import DeepSeekSummarizer, SummarizationError, fit_description
from backend.knowledge.web_fetcher import WebContentError, WebPageFetcher
from backend.models.knowledge import AttractionKnowledge


@pytest.mark.asyncio
async def test_web_fetcher_extracts_visible_text():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><style>hidden</style><body><h1>西湖</h1><p>三面云山，中涵碧水，拥有丰富的人文景观。</p>"
            "<p>适合自然风光、历史文化和摄影主题游览。湖区拥有堤、岛、塔和众多公园景点，"
            "自然景观与历代文化遗存相互融合，适合步行游览和慢节奏体验。</p></body></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await WebPageFetcher(client=client).fetch("https://example.com/west-lake")

    assert "三面云山" in content
    assert "hidden" not in content


@pytest.mark.asyncio
async def test_web_fetcher_rejects_non_https_source():
    with pytest.raises(WebContentError, match="HTTPS"):
        await WebPageFetcher().fetch("http://example.com")


@pytest.mark.asyncio
async def test_web_fetcher_wraps_network_errors_without_url_details():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebContentError, match="网络请求失败"):
            await WebPageFetcher(client=client, max_retries=2).fetch("https://example.com/source")


@pytest.mark.asyncio
async def test_deepseek_summarizer_does_not_expose_key_in_error():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summarizer = DeepSeekSummarizer(
            "secret-key",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            client=client,
        )
        with pytest.raises(SummarizationError) as error:
            await summarizer.summarize(name="西湖", city="杭州市", category=None, source_text="有效正文" * 100)

    assert "secret-key" not in str(error.value)


def test_description_length_is_enforced_by_code():
    content = "这是完整的一句话，用于说明景点特色和游览体验。" * 20

    result = fit_description(content)

    assert 100 <= len(result) <= 220
    assert result.endswith("。")


@pytest.mark.asyncio
async def test_enrichment_reuses_existing_knowledge_without_external_calls():
    existing = AttractionKnowledge(
        id=1,
        poi_id="A1",
        description="已有的可靠景点知识描述，内容足够完整，可以直接复用，无需重新调用外部服务。",
        source_url="https://example.com/source",
        updated_at=datetime.now(),
    )

    class Repository:
        def find(self, poi_id):
            return existing

    class MustNotCall:
        def __getattr__(self, name):
            raise AssertionError(f"external dependency called: {name}")

    class StatusStore:
        statuses = []

        def mark_knowledge_status(self, poi_id, status, error=None):
            self.statuses.append((poi_id, status, error))

    status_store = StatusStore()
    result = await KnowledgeEnrichmentService(
        sources=MustNotCall(),
        fetcher=MustNotCall(),
        summarizer=MustNotCall(),
        repository=Repository(),
        status_store=status_store,
    ).enrich(poi_id="A1", name="西湖", city="杭州市", category=None)

    assert result == existing
    assert status_store.statuses == [("A1", "ready", None)]


@pytest.mark.asyncio
async def test_enrichment_fetches_summarizes_and_persists_missing_knowledge():
    calls = []

    class Sources:
        async def find_source(self, name, city):
            calls.append(("source", name))
            return "https://example.com/source"

    class Fetcher:
        async def fetch(self, url):
            calls.append(("fetch", url))
            return "权威来源正文"

    class Summarizer:
        async def summarize(self, **kwargs):
            calls.append(("summarize", kwargs["name"]))
            return "基于权威来源生成的景点知识描述，包含景点特色、适合的兴趣类型以及主要游览体验。"

    class Repository:
        def find(self, poi_id):
            return None

        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs["poi_id"]))
            return AttractionKnowledge(id=1, updated_at=datetime.now(), **kwargs)

    class StatusStore:
        def mark_knowledge_status(self, poi_id, status, error=None):
            calls.append((status, poi_id))

    result = await KnowledgeEnrichmentService(
        sources=Sources(),
        fetcher=Fetcher(),
        summarizer=Summarizer(),
        repository=Repository(),
        status_store=StatusStore(),
    ).enrich(poi_id="A1", name="西湖", city="杭州市", category="风景名胜")

    assert result.poi_id == "A1"
    assert [call[0] for call in calls] == ["processing", "source", "fetch", "summarize", "upsert", "ready"]


@pytest.mark.asyncio
async def test_enrichment_marks_failure():
    statuses = []

    class Repository:
        def find(self, poi_id):
            return None

    class Sources:
        async def find_source(self, name, city):
            raise LookupError("没有可靠来源")

    class StatusStore:
        def mark_knowledge_status(self, poi_id, status, error=None):
            statuses.append((status, error))

    with pytest.raises(LookupError):
        await KnowledgeEnrichmentService(
            sources=Sources(),
            fetcher=None,
            summarizer=None,
            repository=Repository(),
            status_store=StatusStore(),
        ).enrich(poi_id="A1", name="未知景点", city="杭州市", category=None)

    assert statuses[0] == ("processing", None)
    assert statuses[1] == ("failed", "没有可靠来源")


@pytest.mark.asyncio
async def test_nationwide_source_prefers_curated_without_searching():
    class SearchMustNotRun:
        async def find_source(self, attraction_name, city):
            raise AssertionError("web search should not run")

    discovery = NationwideSourceDiscovery(CuratedSourceDiscovery(), SearchMustNotRun())

    result = await discovery.find_source("杭州西湖风景名胜区", "杭州市")

    assert result.url.startswith("https://sjfw.mct.gov.cn/")
    assert result.extracted_text is None


@pytest.mark.asyncio
async def test_tavily_source_accepts_relevant_government_result_only():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://travel.example.com/lingyin",
                        "title": "灵隐寺旅游攻略",
                        "content": "热门景点攻略",
                    },
                    {
                        "url": "https://wgly.hangzhou.gov.cn/art/lingyin.html",
                        "title": "灵隐寺景区介绍",
                        "content": "杭州市文化广电旅游局发布的景区资料",
                        "raw_content": "灵隐寺是杭州著名的人文景观。" * 20,
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TavilyGovernmentSourceDiscovery(
            "test-search-key", base_url="https://search.test", client=client
        ).find_source("灵隐寺", "杭州市")

    assert result.url == "https://wgly.hangzhou.gov.cn/art/lingyin.html"
    assert len(result.extracted_text) >= 80
    assert requests[0].url.path == "/search"
    assert "灵隐寺" in requests[0].read().decode()
    assert '"include_domains":["gov.cn"]' in requests[0].read().decode()
    assert requests[0].headers["authorization"] == "Bearer test-search-key"


@pytest.mark.asyncio
async def test_tavily_source_retries_transient_network_failure():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://lyj.baoji.gov.cn/guanshan.html",
                        "title": "关山草原",
                        "content": "关山草原是宝鸡重要的山地草原景区。" * 10,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TavilyGovernmentSourceDiscovery(
            "test-search-key",
            base_url="https://search.test",
            client=client,
            max_retries=2,
        ).find_source("关山草原景区", "宝鸡市")

    assert result.url == "https://lyj.baoji.gov.cn/guanshan.html"
    assert attempts == 2


@pytest.mark.asyncio
async def test_tavily_source_matches_specific_forest_park_suffix():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.eco.gov.cn/news_info/40438.html",
                        "title": "太白山",
                        "content": "太白山拥有丰富的自然景观与森林生态资源。" * 10,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TavilyGovernmentSourceDiscovery(
            "test-search-key",
            base_url="https://search.test",
            client=client,
        ).find_source("太白山国家森林公园", "宝鸡市")

    assert result.url == "https://www.eco.gov.cn/news_info/40438.html"


@pytest.mark.asyncio
async def test_tavily_source_rejects_irrelevant_or_non_government_results():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://wgly.hangzhou.gov.cn/other.html",
                        "title": "其他景区介绍",
                        "content": "与目标景点无关",
                    },
                    {
                        "url": "https://example.com/lingyin",
                        "title": "灵隐寺官方网站",
                        "content": "自称官方网站",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceNotFoundError, match="可验证"):
            await TavilyGovernmentSourceDiscovery(
                "test-search-key", base_url="https://search.test", client=client
            ).find_source("灵隐寺", "杭州市")


@pytest.mark.asyncio
async def test_tavily_source_requires_configuration_without_network_call():
    with pytest.raises(SourceNotFoundError, match="WEB_SEARCH_API_KEY"):
        await TavilyGovernmentSourceDiscovery(None).find_source("灵隐寺", "杭州市")


@pytest.mark.asyncio
async def test_enrichment_uses_search_extracted_text_when_official_page_blocks_fetch():
    calls = []

    class Sources:
        async def find_source(self, name, city):
            return DiscoveredSource(
                url="https://wgly.hangzhou.gov.cn/lingyin.html",
                extracted_text="灵隐寺是杭州历史悠久的人文景观，拥有丰富的佛教文化与建筑遗存。" * 10,
            )

    class Fetcher:
        async def fetch(self, url):
            calls.append(("fetch", url))
            raise WebContentError("官网拒绝访问")

    class Summarizer:
        async def summarize(self, **kwargs):
            calls.append(("summarize", kwargs["source_text"]))
            return "基于搜索服务提取的同一权威页面正文生成的景点知识摘要，保留原始政府网页地址用于来源追溯。"

    class Repository:
        def find(self, poi_id):
            return None

        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs["source_url"]))
            return AttractionKnowledge(id=1, updated_at=datetime.now(), **kwargs)

    result = await KnowledgeEnrichmentService(
        sources=Sources(),
        fetcher=Fetcher(),
        summarizer=Summarizer(),
        repository=Repository(),
    ).enrich(poi_id="A1", name="灵隐寺", city="杭州市", category="寺庙")

    assert result.source_url == "https://wgly.hangzhou.gov.cn/lingyin.html"
    assert [call[0] for call in calls] == ["summarize", "upsert"]
