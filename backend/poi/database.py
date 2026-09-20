"""Database-backed attraction provider used as the live-search fallback."""

from backend.attractions.dedupe import dedupe_attractions
from backend.models.attraction import AttractionPOI


class DatabaseAttractionProvider:
    def __init__(self, attraction_repository, catalog_repository, *, interests=None):
        self.attraction_repository = attraction_repository
        self.catalog_repository = catalog_repository
        self.interests = list(interests or [])

    async def search_attractions(self, city: str, *, limit: int = 30) -> list[AttractionPOI]:
        city_row = self.catalog_repository.find_city(city)
        if city_row is None:
            return []
        rows = self.attraction_repository.list_for_city(
            city_row.id,
            interests=self.interests,
            statuses={"ready"},
            limit=limit,
        )
        return dedupe_attractions(
            [
                AttractionPOI(
                    poi_id=row.poi_id,
                    name=row.name,
                    city=city_row.name,
                    category=row.category,
                    tags=list(row.tags or []),
                    description=row.description,
                    visit_duration_minutes=row.visit_duration_minutes,
                    longitude=row.longitude,
                    latitude=row.latitude,
                )
                for row in rows
            ]
        )


__all__ = ["DatabaseAttractionProvider"]
