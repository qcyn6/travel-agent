import logging
from typing import Protocol

from backend.attractions.dedupe import dedupe_attractions
from backend.models.attraction import AttractionPOI


logger = logging.getLogger(__name__)


class AttractionProvider(Protocol):
    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]: ...


class FallbackAttractionProvider:
    """Use a live provider first and the local catalog on failure or no result."""

    def __init__(self, primary: AttractionProvider, fallback: AttractionProvider):
        self.primary = primary
        self.fallback = fallback
        self.last_source = "unknown"

    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]:
        try:
            attractions = dedupe_attractions(
                await self.primary.search_attractions(city, limit=limit)
            )
            if attractions:
                self.last_source = "primary"
                return attractions
            raise RuntimeError("高德 MCP 未返回景点")
        except Exception as exc:
            self.last_source = "fallback"
            logger.warning(
                "高德 MCP 景点搜索失败，改用本地数据库：city=%s error=%s",
                city,
                type(exc).__name__,
            )
            return await self.fallback.search_attractions(city, limit=limit)


class AttractionWriter(Protocol):
    def upsert_many(self, city_id: int, attractions: list[AttractionPOI]) -> int: ...


class AttractionService:
    def __init__(self, provider: AttractionProvider, repository: AttractionWriter):
        self.provider = provider
        self.repository = repository

    async def search_and_store(self, *, city_id: int, city_name: str, limit: int = 30) -> list[AttractionPOI]:
        attractions = dedupe_attractions(
            await self.provider.search_attractions(city_name, limit=limit)
        )
        self.repository.upsert_many(city_id, attractions)
        return attractions
