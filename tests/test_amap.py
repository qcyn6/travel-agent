import httpx
import pytest

from backend.models.attraction import AttractionPOI
from backend.poi.amap import AmapAPIError, AmapAttractionClient, clean_attractions


def test_clean_attractions_normalizes_filters_and_deduplicates():
    raw = [
        {
            "id": "A1",
            "name": "西湖风景名胜区",
            "cityname": "杭州市",
            "type": "风景名胜;国家级景点",
            "address": "西湖区",
            "location": "120.148,30.243",
            "business": {
                "rating": "4.9",
                "opentime_week": "周一至周日:08:00-18:00",
                "opentime_today": "08:00-18:00",
            },
        },
        {"id": "A2", "name": "西湖风景名胜区", "location": "120.148,30.243"},
        {"id": "A3", "name": "西湖景区停车场", "location": "120.1,30.2"},
        {"id": "", "name": "无编号景点"},
    ]

    result = clean_attractions(raw, "杭州市")

    assert len(result) == 1
    assert result[0].poi_id == "A1"
    assert result[0].longitude == 120.148
    assert result[0].latitude == 30.243
    assert result[0].rating == 4.9
    assert result[0].opening_hours_weekly == "周一至周日:08:00-18:00"
    assert result[0].opening_hours_today == "08:00-18:00"
    assert result[0].opening_hours_status == "available"


@pytest.mark.asyncio
async def test_opening_hours_enrichment_requests_business_fields_and_exact_matches():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "灵隐寺"
        assert request.url.params["show_fields"] == "business"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "A1",
                        "name": "灵隐寺",
                        "business": {
                            "opentime_week": "周一至周日:07:00-18:00",
                            "opentime_today": "07:00-18:00",
                        },
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AmapAttractionClient("test-key", client=http_client)
        candidate = AttractionPOI(poi_id="LOCAL1", name="灵隐寺", city="杭州市")
        result = await client.enrich_opening_hours([candidate])

    assert result[0].opening_hours_weekly == "周一至周日:07:00-18:00"
    assert result[0].opening_hours_today == "07:00-18:00"
    assert result[0].opening_hours_status == "available"


@pytest.mark.asyncio
async def test_opening_hours_enrichment_does_not_attach_nearby_name_match():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "A2",
                        "name": "灵隐寺游客中心",
                        "business": {"opentime_week": "周一至周日:00:00-23:59"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AmapAttractionClient("test-key", client=http_client)
        candidate = AttractionPOI(poi_id="LOCAL1", name="灵隐寺", city="杭州市")
        result = await client.enrich_opening_hours([candidate])

    assert result[0].opening_hours_weekly is None
    assert result[0].opening_hours_status == "unavailable"


@pytest.mark.asyncio
async def test_search_uses_city_limit_and_maps_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["region"] == "杭州市"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["page_size"] == "25"
        assert request.url.params["types"] == "风景名胜"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "pois": [{"id": "A1", "name": "灵隐寺", "cityname": "杭州市", "location": "120.102,30.240"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AmapAttractionClient("test-key", client=http_client)
        result = await client.search_attractions("杭州市", limit=10)

    assert [item.name for item in result] == ["灵隐寺"]


@pytest.mark.asyncio
async def test_search_reports_provider_error_without_key_in_message():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AmapAttractionClient("secret-test-key", client=http_client)
        with pytest.raises(AmapAPIError) as error:
            await client.search_attractions("杭州市")

    assert error.value.infocode == "10001"
    assert "secret-test-key" not in str(error.value)


@pytest.mark.asyncio
async def test_search_retries_transient_server_errors():
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "1", "pois": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AmapAttractionClient("test-key", client=http_client, max_retries=3)
        result = await client.search_attractions("杭州市")

    assert result == []
    assert calls == 3
