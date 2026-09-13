from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from backend.database.tables import AttractionFactsRow
from backend.models.attraction_facts import AttractionFacts, OpeningHours


class AttractionFactsRepository:
    def __init__(self, session: Session):
        self.session = session

    def find(self, poi_id: str) -> AttractionFacts | None:
        row = self.session.scalar(
            select(AttractionFactsRow).where(AttractionFactsRow.poi_id == poi_id)
        )
        if row is None:
            return None
        return AttractionFacts(
            poi_id=row.poi_id,
            environment=row.environment,
            estimated_visit_minutes=row.estimated_visit_minutes,
            duration_source=row.duration_source,
            opening_hours=OpeningHours.model_validate(row.opening_hours),
            source_url=row.source_url,
            confidence=row.confidence,
            checked_at=row.checked_at,
        )

    def upsert(self, facts: AttractionFacts) -> AttractionFacts:
        values = facts.model_dump(mode="python")
        values["opening_hours"] = facts.opening_hours.model_dump(mode="json")
        statement = insert(AttractionFactsRow).values(**values)
        try:
            self.session.execute(
                statement.on_duplicate_key_update(
                    environment=statement.inserted.environment,
                    estimated_visit_minutes=statement.inserted.estimated_visit_minutes,
                    duration_source=statement.inserted.duration_source,
                    opening_hours=statement.inserted.opening_hours,
                    source_url=statement.inserted.source_url,
                    confidence=statement.inserted.confidence,
                    checked_at=statement.inserted.checked_at,
                    updated_at=func.now(),
                )
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        stored = self.find(facts.poi_id)
        if stored is None:
            raise RuntimeError("景点事实写入后无法读取")
        return stored
