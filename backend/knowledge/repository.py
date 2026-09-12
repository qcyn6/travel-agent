from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.database.tables import AttractionKnowledgeRow, AttractionRow, CityRow
from backend.models.knowledge import AttractionKnowledge
from backend.models.retrieval import AttractionDocument


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def find(self, poi_id: str) -> AttractionKnowledge | None:
        row = self.session.scalar(
            select(AttractionKnowledgeRow).where(AttractionKnowledgeRow.poi_id == poi_id)
        )
        return AttractionKnowledge.model_validate(row) if row else None

    def upsert(self, *, poi_id: str, description: str, source_url: str) -> AttractionKnowledge:
        statement = insert(AttractionKnowledgeRow).values(
            poi_id=poi_id,
            description=description,
            source_url=source_url,
        )
        try:
            self.session.execute(
                statement.on_duplicate_key_update(
                    description=statement.inserted.description,
                    source_url=statement.inserted.source_url,
                    updated_at=func.now(),
                )
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        knowledge = self.find(poi_id)
        if knowledge is None:
            raise RuntimeError("景点知识写入后无法读取")
        return knowledge

    def list_documents(self, city: str) -> list[AttractionDocument]:
        rows = self.session.execute(
            select(AttractionKnowledgeRow, AttractionRow, CityRow)
            .join(AttractionRow, AttractionRow.poi_id == AttractionKnowledgeRow.poi_id)
            .join(CityRow, CityRow.id == AttractionRow.city_id)
            .where(CityRow.name == city, AttractionRow.knowledge_status == "ready")
            .order_by(AttractionRow.id)
        ).all()
        return [
            AttractionDocument(
                poi_id=attraction.poi_id,
                name=attraction.name,
                city=city_row.name,
                category=attraction.category,
                description=knowledge.description,
                source_url=knowledge.source_url,
            )
            for knowledge, attraction, city_row in rows
        ]


def get_knowledge_repository(session: Session = Depends(get_db)) -> KnowledgeRepository:
    return KnowledgeRepository(session)
