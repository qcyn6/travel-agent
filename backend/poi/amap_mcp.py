"""Amap MCP adapter for attraction discovery and candidate enrichment.

The official Amap MCP endpoint exposes search results through the
``maps_text_search`` and ``maps_search_detail`` tools. The planning path uses
the enrichment method only after local retrieval has selected candidates. The
discovery method remains available for compatibility with explicit
attraction-list API callers, but it is not used to expand the planning
candidate set.
"""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.attractions.metadata import infer_attraction_category, infer_interest_tags
from backend.models.attraction import AttractionPOI
from backend.observability import observe_external_request
from backend.poi.amap import (
    AmapAPIError,
    AmapConfigurationError,
    _NON_ATTRACTION_NAME_PARTS,
    _coordinates,
    _optional_float,
    _optional_text,
)


logger = logging.getLogger(__name__)


class AmapMcpAttractionClient:
    """Search real attractions through Amap's official Streamable HTTP MCP."""

    def __init__(
        self,
        api_key: str | None,
        *,
        endpoint: str = "https://mcp.amap.com/mcp",
        timeout_seconds: float = 15.0,
        sse_read_timeout_seconds: float = 20.0,
        detail_concurrency: int = 5,
        keywords: str = "风景名胜",
        cache=None,
        cache_ttl_seconds: int = 86400,
    ):
        if not api_key:
            raise AmapConfigurationError("未配置 AMAP_API_KEY")
        if not endpoint.strip():
            raise AmapConfigurationError("未配置高德 MCP 地址")
        if not keywords.strip():
            raise ValueError("keywords 不能为空")

        self._api_key = api_key
        self._endpoint = endpoint.strip()
        self._timeout_seconds = timeout_seconds
        self._sse_read_timeout_seconds = sse_read_timeout_seconds
        self._detail_concurrency = max(1, int(detail_concurrency))
        self._keywords = keywords.strip()
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]:
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间")
        city = city.strip()
        if not city:
            raise ValueError("city 不能为空")

        cache_key = f"poi:mcp:{city}:{self._keywords}:{limit}"
        if self._cache is not None:
            cached = await self._cache.get_json(cache_key)
            if isinstance(cached, list):
                try:
                    return [AttractionPOI.model_validate(item) for item in cached]
                except (TypeError, ValueError):
                    logger.warning("高德 MCP 景点缓存格式异常，忽略缓存")

        try:
            async with observe_external_request("amap_mcp", "poi_search"):
                attractions = await self._search_remote(city, limit=limit)
        except AmapAPIError:
            raise
        except Exception as exc:
            # Do not expose the MCP endpoint or API key in the public error.
            raise AmapAPIError("高德 MCP 景点查询失败") from exc

        if self._cache is not None and attractions:
            await self._cache.set_json(
                cache_key,
                [item.model_dump(mode="json") for item in attractions],
                ttl_seconds=self._cache_ttl_seconds,
            )
        return attractions

    async def enrich_attractions(
        self,
        attractions: Sequence[AttractionPOI],
    ) -> list[AttractionPOI]:
        """Fetch Amap facts for already-selected local candidates.

        This method deliberately does not add POIs to the result. The local
        database decides which attractions are eligible for planning; Amap is
        only used to resolve the selected attraction name to a live POI and to
        enrich it with current factual fields such as coordinates and address.
        """

        candidates = list(attractions)
        if not candidates:
            return []

        try:
            async with streamablehttp_client(
                self._endpoint_with_key(),
                timeout=self._timeout_seconds,
                sse_read_timeout=self._sse_read_timeout_seconds,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    semaphore = asyncio.Semaphore(self._detail_concurrency)

                    async def enrich(candidate: AttractionPOI) -> AttractionPOI:
                        async with semaphore:
                            try:
                                remote_id = await self._resolve_remote_id(
                                    session,
                                    candidate,
                                )
                                if not remote_id:
                                    return candidate
                                detail_result = await session.call_tool(
                                    "maps_search_detail",
                                    {"id": remote_id},
                                )
                                detail_payload = _tool_payload(
                                    detail_result,
                                    "高德 MCP 景点详情",
                                )
                                detailed = _from_mcp_record(
                                    detail_payload,
                                    candidate.city,
                                )
                                if detailed is None:
                                    return candidate
                                return _merge_external_details(candidate, detailed)
                            except Exception as exc:
                                # A detail failure must not discard a candidate
                                # that already passed the local selection rules.
                                logger.warning(
                                    "高德 MCP 景点详情补充失败：poi=%s error=%s",
                                    candidate.poi_id,
                                    type(exc).__name__,
                                )
                                return candidate

                    return await asyncio.gather(*(enrich(item) for item in candidates))
        except AmapAPIError:
            raise
        except Exception as exc:
            raise AmapAPIError("高德 MCP 景点详情补充失败") from exc

    async def _resolve_remote_id(self, session, candidate: AttractionPOI) -> str | None:
        """Resolve one selected local attraction to an Amap POI ID by name."""

        result = await session.call_tool(
            "maps_text_search",
            {
                "keywords": candidate.name,
                "city": candidate.city,
                "citylimit": True,
            },
        )
        payload = _tool_payload(result, "高德 MCP 景点文本搜索")
        raw_pois = payload.get("pois")
        if not isinstance(raw_pois, list):
            return None

        expected_name = _normalize_name(candidate.name)
        for raw in raw_pois:
            if not isinstance(raw, Mapping):
                continue
            poi_id = _optional_text(raw.get("id"))
            name = _optional_text(raw.get("name"))
            if poi_id and name and _normalize_name(name) == expected_name:
                return poi_id
        return None

    async def _search_remote(self, city: str, *, limit: int) -> list[AttractionPOI]:
        async with streamablehttp_client(
            self._endpoint_with_key(),
            timeout=self._timeout_seconds,
            sse_read_timeout=self._sse_read_timeout_seconds,
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                search_result = await session.call_tool(
                    "maps_text_search",
                    {
                        "keywords": self._keywords,
                        "city": city,
                        "citylimit": True,
                    },
                )
                search_payload = _tool_payload(search_result, "高德 MCP 文本搜索")
                raw_pois = search_payload.get("pois")
                if not isinstance(raw_pois, list):
                    raise AmapAPIError("高德 MCP 景点搜索返回格式异常")

                candidates = _search_candidates(raw_pois, city)[:limit]
                if not candidates:
                    return []

                # The text-search tool currently returns names and IDs but not
                # coordinates. Details are fetched concurrently so the live
                # POIs can participate in route enrichment as well.
                semaphore = asyncio.Semaphore(self._detail_concurrency)

                async def resolve(candidate: AttractionPOI) -> AttractionPOI:
                    if candidate.longitude is not None and candidate.latitude is not None:
                        return _finalize(candidate)
                    async with semaphore:
                        try:
                            detail_result = await session.call_tool(
                                "maps_search_detail",
                                {"id": candidate.poi_id},
                            )
                            detail_payload = _tool_payload(
                                detail_result,
                                "高德 MCP 景点详情",
                            )
                            detailed = _from_mcp_record(detail_payload, city)
                            if detailed is not None:
                                return detailed
                        except Exception as exc:
                            # A valid text-search result is still useful when a
                            # single detail lookup is unavailable. The local
                            # fallback handles complete MCP failures.
                            logger.warning(
                                "高德 MCP 景点详情获取失败：poi=%s error=%s",
                                candidate.poi_id,
                                type(exc).__name__,
                            )
                        return _finalize(candidate)

                return await asyncio.gather(*(resolve(item) for item in candidates))

    def _endpoint_with_key(self) -> str:
        parsed = urlsplit(self._endpoint)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(name == "key" for name, _ in query):
            query.append(("key", self._api_key))
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )


def _search_candidates(raw_pois: list[object], city: str) -> list[AttractionPOI]:
    result: list[AttractionPOI] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in raw_pois:
        if not isinstance(raw, Mapping):
            continue
        poi_id = _optional_text(raw.get("id"))
        name = _optional_text(raw.get("name"))
        if not poi_id or not name or any(part in name for part in _NON_ATTRACTION_NAME_PARTS):
            continue
        normalized_name = "".join(name.split()).casefold()
        if poi_id in seen_ids or normalized_name in seen_names:
            continue
        seen_ids.add(poi_id)
        seen_names.add(normalized_name)
        result.append(_from_mcp_record(raw, city) or AttractionPOI(
            poi_id=poi_id,
            name=name,
            city=city,
        ))
    return result


def _from_mcp_record(raw: Mapping[str, object], requested_city: str) -> AttractionPOI | None:
    poi_id = _optional_text(raw.get("id"))
    name = _optional_text(raw.get("name"))
    if not poi_id or not name or any(part in name for part in _NON_ATTRACTION_NAME_PARTS):
        return None

    longitude, latitude = _coordinates(raw.get("location"))
    category = _optional_text(raw.get("type"))
    address = _optional_text(raw.get("address"))
    city = _optional_text(raw.get("city")) or _optional_text(raw.get("cityname")) or requested_city
    rating = _optional_float(raw.get("rating"))
    business = raw.get("business") if isinstance(raw.get("business"), Mapping) else {}
    if rating is None:
        rating = _optional_float(business.get("rating"))
    opening_hours_weekly = _optional_text(business.get("opentime_week"))
    opening_hours_today = _optional_text(business.get("opentime_today"))

    return _finalize(
        AttractionPOI(
            poi_id=poi_id,
            name=name,
            city=city,
            category=category,
            address=address,
            longitude=longitude,
            latitude=latitude,
            rating=rating,
            opening_hours_weekly=opening_hours_weekly,
            opening_hours_today=opening_hours_today,
            opening_hours_status=(
                "available"
                if opening_hours_weekly or opening_hours_today
                else "unavailable"
            ),
        )
    )


def _finalize(attraction: AttractionPOI) -> AttractionPOI:
    category = infer_attraction_category(attraction.name, attraction.category)
    tags = infer_interest_tags(attraction.name, category)
    facts = [attraction.name, f"类别：{category}"]
    if attraction.address:
        facts.append(f"地址：{attraction.address}")
    return attraction.model_copy(
        update={
            "category": category,
            "tags": tags,
            # Keep the MCP result factual while making a newly discovered POI
            # immediately searchable by the existing local RAG pipeline.
            "description": "；".join(facts),
        }
    )


def _merge_external_details(local: AttractionPOI, external: AttractionPOI) -> AttractionPOI:
    """Merge factual Amap fields without changing local planning fields."""

    return local.model_copy(
        update={
            "category": external.category or local.category,
            "address": external.address or local.address,
            "longitude": external.longitude if external.longitude is not None else local.longitude,
            "latitude": external.latitude if external.latitude is not None else local.latitude,
            "rating": external.rating if external.rating is not None else local.rating,
            "opening_hours_weekly": (
                external.opening_hours_weekly or local.opening_hours_weekly
            ),
            "opening_hours_today": (
                external.opening_hours_today or local.opening_hours_today
            ),
            "opening_hours_status": (
                "available"
                if external.opening_hours_weekly or external.opening_hours_today
                else local.opening_hours_status
            ),
        }
    )


def _normalize_name(name: str) -> str:
    return "".join(character for character in name.strip().casefold() if character.isalnum())


def _tool_payload(result, label: str) -> dict[str, object]:
    if getattr(result, "isError", False):
        raise AmapAPIError(f"{label}失败")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        return dict(structured)

    for block in getattr(result, "content", []) or []:
        text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        payload = _parse_json(text)
        if isinstance(payload, Mapping):
            return dict(payload)
    raise AmapAPIError(f"{label}返回格式异常")


def _parse_json(text: str):
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`").strip()
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()
    try:
        return json.loads(normalized)
    except (TypeError, ValueError):
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(normalized[start : end + 1])
        except (TypeError, ValueError):
            return None


__all__ = ["AmapMcpAttractionClient"]
