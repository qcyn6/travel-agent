import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.attractions.metadata import infer_attraction_category
import backend.poi.amap_mcp as amap_mcp
from backend.models.attraction import AttractionPOI
from backend.services import FallbackAttractionProvider


class FakeToolResult:
    def __init__(self, payload, *, is_error=False):
        self.isError = is_error
        self.structuredContent = None
        self.content = [
            SimpleNamespace(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ]


class FakeSession:
    calls = []

    def __init__(self, read_stream, write_stream):
        del read_stream, write_stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        return None

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "maps_text_search":
            return FakeToolResult(
                {
                    "pois": [
                        {"id": "A1", "name": "西湖"},
                        {"id": "A2", "name": "西湖停车场"},
                        {"id": "A3", "name": "灵隐寺"},
                    ]
                }
            )
        details = {
            "A1": {
                "id": "A1",
                "name": "西湖",
                "city": "杭州市",
                "type": "风景名胜",
                "address": "西湖区",
                "location": "120.148,30.243",
                "rating": "4.9",
            },
            "A3": {
                "id": "A3",
                "name": "灵隐寺",
                "city": "杭州市",
                "type": "寺庙",
                "address": "法云弄1号",
                "location": "120.101,30.240",
                "rating": "4.8",
            },
        }
        return FakeToolResult(details[arguments["id"]])


class FakeConnector:
    def __init__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs

    async def __aenter__(self):
        return object(), object(), None

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_mcp_search_uses_official_tools_and_normalizes_details(monkeypatch):
    FakeSession.calls = []
    connectors = []

    def connector(url, **kwargs):
        item = FakeConnector(url, **kwargs)
        connectors.append(item)
        return item

    monkeypatch.setattr(amap_mcp, "streamablehttp_client", connector)
    monkeypatch.setattr(amap_mcp, "ClientSession", FakeSession)

    client = amap_mcp.AmapMcpAttractionClient(
        "secret-key",
        endpoint="https://mcp.amap.com/mcp",
        detail_concurrency=2,
    )
    result = await client.search_attractions("杭州市", limit=2)

    assert [item.poi_id for item in result] == ["A1", "A3"]
    assert result[0].longitude == 120.148
    assert result[0].latitude == 30.243
    assert result[0].rating == 4.9
    assert result[0].description.startswith("西湖；类别：")
    assert result[0].description.endswith("；地址：西湖区")
    assert FakeSession.calls[0] == (
        "maps_text_search",
        {"keywords": "风景名胜", "city": "杭州市", "citylimit": True},
    )
    assert {call[0] for call in FakeSession.calls[1:]} == {"maps_search_detail"}
    assert parse_qs(urlsplit(connectors[0].url).query)["key"] == ["secret-key"]
    assert connectors[0].kwargs["timeout"] == 15.0


@pytest.mark.asyncio
async def test_mcp_enrichment_only_resolves_selected_candidates(monkeypatch):
    class EnrichmentSession:
        calls = []

        def __init__(self, read_stream, write_stream):
            del read_stream, write_stream

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "maps_text_search":
                return FakeToolResult(
                    {
                        "pois": [
                            {"id": "AMAP-XIHU", "name": "西湖"},
                            {"id": "AMAP-OTHER", "name": "其他景点"},
                        ]
                    }
                )
            return FakeToolResult(
                {
                    "id": "AMAP-XIHU",
                    "name": "西湖",
                    "city": "杭州市",
                    "type": "湖泊",
                    "address": "西湖区",
                    "location": "120.148,30.243",
                    "rating": "4.9",
                }
            )

    class Connector:
        async def __aenter__(self):
            return object(), object(), None

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(amap_mcp, "streamablehttp_client", lambda *_args, **_kwargs: Connector())
    monkeypatch.setattr(amap_mcp, "ClientSession", EnrichmentSession)

    client = amap_mcp.AmapMcpAttractionClient("secret-key")
    local = AttractionPOI(
        poi_id="local-xihu",
        name="西湖",
        city="杭州市",
        category="本地分类",
        tags=["自然"],
        description="本地维护的景点描述",
        visit_duration_minutes=120,
    )

    result = await client.enrich_attractions([local])

    assert result[0].poi_id == "local-xihu"
    assert result[0].category == infer_attraction_category("西湖", "湖泊")
    assert result[0].longitude == 120.148
    assert result[0].latitude == 30.243
    assert result[0].address == "西湖区"
    assert result[0].tags == ["自然"]
    assert result[0].description == "本地维护的景点描述"
    assert result[0].visit_duration_minutes == 120
    assert EnrichmentSession.calls == [
        (
            "maps_text_search",
            {"keywords": "西湖", "city": "杭州市", "citylimit": True},
        ),
        ("maps_search_detail", {"id": "AMAP-XIHU"}),
    ]


@pytest.mark.asyncio
async def test_fallback_provider_uses_database_when_mcp_fails():
    local = AttractionPOI(poi_id="L1", name="本地景点", city="杭州市")

    class Primary:
        async def search_attractions(self, city, *, limit):
            raise RuntimeError(f"{city}:{limit}")

    class Fallback:
        async def search_attractions(self, city, *, limit):
            assert city == "杭州市"
            assert limit == 5
            return [local]

    provider = FallbackAttractionProvider(Primary(), Fallback())

    assert await provider.search_attractions("杭州市", limit=5) == [local]
    assert provider.last_source == "fallback"
