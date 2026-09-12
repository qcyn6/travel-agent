from typing import Protocol

from backend.models.attraction import AttractionPOI


class AttractionProvider(Protocol):
    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]: ...


class AttractionWriter(Protocol):
    def upsert_many(self, city_id: int, attractions: list[AttractionPOI]) -> int: ...


class AttractionService:
    def __init__(self, provider: AttractionProvider, repository: AttractionWriter):
        self.provider = provider
        self.repository = repository

    async def search_and_store(self, *, city_id: int, city_name: str, limit: int = 30) -> list[AttractionPOI]:
        attractions = await self.provider.search_attractions(city_name, limit=limit)
        self.repository.upsert_many(city_id, attractions)
        return attractions
