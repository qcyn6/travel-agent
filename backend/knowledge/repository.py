from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database import get_db
from backend.database.tables import AttractionRow, CityRow
from backend.models.knowledge import AttractionKnowledge
from backend.models.retrieval import AttractionDocument
from backend.tag_matching import compact_tag_match_score


class KnowledgeRepository:
    """Read and update attraction descriptions stored on the main POI row."""

    def __init__(self, session: Session, *, core_only: bool = False):
        # Kept as a no-op for callers from the former 4A/5A implementation.
        del core_only
        self.session = session

    def find(self, poi_id: str) -> AttractionKnowledge | None:
        row = self.session.scalar(
            select(AttractionRow).where(
                AttractionRow.poi_id == poi_id,
            )
        )
        if row is None or not row.description.strip():
            return None
        return AttractionKnowledge(poi_id=row.poi_id, description=row.description)

    def upsert(
        self,
        *,
        poi_id: str,
        description: str,
        source_url: str | None = None,
    ) -> AttractionKnowledge:
        del source_url
        row = self.session.scalar(
            select(AttractionRow).where(AttractionRow.poi_id == poi_id)
        )
        if row is None:
            raise LookupError(f"景点 POI 不存在：{poi_id}")
        row.description = description.strip()
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        knowledge = self.find(poi_id)
        if knowledge is None:
            raise RuntimeError("景点描述写入后无法读取")
        return knowledge

    def list_documents(
        self,
        city: str,
        *,
        interests: Sequence[str] | None = None,
    ) -> list[AttractionDocument]:
        rows = list(
            self.session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .where(
                    CityRow.name == city,
                    AttractionRow.description.is_not(None),
                    AttractionRow.description != "",
                )
            ).all()
        )
        rows.sort(
            key=lambda row_data: (
                -compact_tag_match_score(row_data[0].tags, interests),
                row_data[0].id,
            )
        )
        return [
            AttractionDocument(
                poi_id=attraction.poi_id,
                name=attraction.name,
                city=city_row.name,
                category=attraction.category,
                interest_tags=list(attraction.tags or []),
                longitude=attraction.longitude,
                latitude=attraction.latitude,
                description=attraction.description,
                source_url=None,
                visit_duration_minutes=attraction.visit_duration_minutes,
            )
            for attraction, city_row in rows
        ]

    def search_documents(
        self,
        query: str,
        *,
        city_names: Sequence[str] | None = None,
        limit: int = 5,
    ) -> list[AttractionDocument]:
        """Search existing attraction facts for ordinary-chat answers.

        This is deliberately read-only and uses the current attraction table
        as its source of truth. It does not create a second knowledge store.
        """

        if limit <= 0 or not query.strip():
            return []
        statement = select(AttractionRow, CityRow).join(
            CityRow,
            CityRow.id == AttractionRow.city_id,
        )
        names = [name.strip() for name in (city_names or []) if name.strip()]
        if names:
            statement = statement.where(CityRow.name.in_(names))
        rows = list(self.session.execute(statement).all())
        normalized_query = query.strip().lower()

        def score(item) -> tuple[int, int]:
            attraction, _ = item
            value = 0
            name = attraction.name.strip().lower()
            if name and name in normalized_query:
                value += 100
            if normalized_query and normalized_query in name:
                value += 80
            for tag in attraction.tags or []:
                if str(tag).strip().lower() in normalized_query:
                    value += 12
            category = (attraction.category or "").strip().lower()
            if category and any(part in normalized_query for part in category.split(";")):
                value += 6
            if names:
                value += 1
            return value, -attraction.id

        ranked = sorted(rows, key=score, reverse=True)
        matched = [item for item in ranked if score(item)[0] > 0][:limit]
        return [
            AttractionDocument(
                poi_id=attraction.poi_id,
                name=attraction.name,
                city=city.name,
                category=attraction.category,
                interest_tags=list(attraction.tags or []),
                longitude=attraction.longitude,
                latitude=attraction.latitude,
                description=attraction.description or "",
                source_url=None,
                visit_duration_minutes=attraction.visit_duration_minutes,
            )
            for attraction, city in matched
        ]


def get_knowledge_repository(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> KnowledgeRepository:
    # The dependency keeps the public function signature stable; descriptions
    # no longer depend on settings or an external knowledge source.
    del settings
    return KnowledgeRepository(session)
