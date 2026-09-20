from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from backend.attractions.dedupe import dedupe_attractions
from backend.database import get_db
from backend.database.tables import AttractionRow
from backend.models.attraction import AttractionPOI
from backend.tag_matching import tag_match_score
from backend.tag_vocabulary import canonicalize_tags


def build_attraction_upsert(city_id: int, attractions: Sequence[AttractionPOI]):
    """Build a POI upsert without overwriting local tags or descriptions.

    Amap REST/MCP is optional at the repository boundary and only supplies
    basic POI coordinates/category data. Locally prepared tags and
    descriptions are the source used by planning, so an external refresh must
    not erase them with empty provider fields.
    """

    rows = []
    for item in attractions:
        tags = canonicalize_tags(item.tags)
        rows.append(
            {
                "poi_id": item.poi_id,
                "city_id": city_id,
                "name": item.name,
                "category": item.category,
                "tags": tags,
                "description": item.description or "",
                "longitude": item.longitude,
                "latitude": item.latitude,
                "visit_duration_minutes": item.visit_duration_minutes or 90,
            }
        )
    statement = insert(AttractionRow).values(rows)
    return statement.on_duplicate_key_update(
        city_id=statement.inserted.city_id,
        name=statement.inserted.name,
        category=statement.inserted.category,
        longitude=statement.inserted.longitude,
        latitude=statement.inserted.latitude,
    )


class AttractionRepository:
    def __init__(self, session: Session, *, core_only: bool = False):
        # "core_only" is accepted for compatibility with older callers. The
        # 4A/5A scope column was removed; the local catalog is now one unified
        # demo dataset.
        del core_only
        self.session = session
        self.core_only = False

    def upsert_many(self, city_id: int, attractions: Sequence[AttractionPOI]) -> int:
        if not attractions:
            return 0
        try:
            self.session.execute(build_attraction_upsert(city_id, attractions))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return len(attractions)

    def find_by_poi_id(self, poi_id: str) -> AttractionRow | None:
        return self.session.scalar(
            select(AttractionRow).where(
                AttractionRow.poi_id == poi_id,
            )
        )

    def list_for_city(
        self,
        city_id: int,
        *,
        interests: Sequence[str] | None = None,
        statuses: set[str] | None = None,
        limit: int = 30,
    ) -> list[AttractionRow]:
        """List attractions ranked by exact shared interest tags.

        This deliberately mirrors destination selection: each matching tag is
        one point, ties are stable by the catalog row id, and neutral items
        remain as a fallback when no tag matches.
        """

        statement = select(AttractionRow).where(
            AttractionRow.city_id == city_id,
        )
        if statuses:
            ready = and_(
                AttractionRow.description.is_not(None),
                AttractionRow.description != "",
            )
            missing = or_(
                AttractionRow.description.is_(None),
                AttractionRow.description == "",
            )
            wants_ready = "ready" in statuses
            wants_missing = bool(statuses & {"pending", "processing", "staged", "failed"})
            if wants_ready and wants_missing:
                statement = statement.where(or_(ready, missing))
            elif wants_ready:
                statement = statement.where(ready)
            elif wants_missing:
                statement = statement.where(missing)
            else:
                statement = statement.where(false())

        rows = list(self.session.scalars(statement))
        normalized_interests = list(interests or [])
        rows.sort(
            key=lambda row: (
                -tag_match_score(row.tags, normalized_interests),
                row.id,
            )
        )
        return dedupe_attractions(rows)[:limit]

    def count_ready_for_city(self, city_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(AttractionRow)
            .where(
                AttractionRow.city_id == city_id,
                AttractionRow.description.is_not(None),
                AttractionRow.description != "",
            )
        )
        return int(self.session.scalar(statement) or 0)

    def has_description(self, poi_id: str) -> bool:
        statement = select(AttractionRow.description).where(
            AttractionRow.poi_id == poi_id,
        )
        description = self.session.scalar(statement)
        return bool(description and description.strip())


def get_attraction_repository(session: Session = Depends(get_db)) -> AttractionRepository:
    return AttractionRepository(session)
