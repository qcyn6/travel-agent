from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.database.tables import AttractionRow
from backend.models.attraction import AttractionPOI


def build_attraction_upsert(city_id: int, attractions: Sequence[AttractionPOI]):
    rows = [
        {
            "poi_id": item.poi_id,
            "city_id": city_id,
            "name": item.name,
            "category": item.category,
            "address": item.address,
            "longitude": item.longitude,
            "latitude": item.latitude,
            "rating": item.rating,
            "source": "amap",
        }
        for item in attractions
    ]
    statement = insert(AttractionRow).values(rows)
    return statement.on_duplicate_key_update(
        city_id=statement.inserted.city_id,
        name=statement.inserted.name,
        category=statement.inserted.category,
        address=statement.inserted.address,
        longitude=statement.inserted.longitude,
        latitude=statement.inserted.latitude,
        rating=statement.inserted.rating,
        source=statement.inserted.source,
        updated_at=func.now(),
    )


class AttractionRepository:
    def __init__(self, session: Session):
        self.session = session

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
        return self.session.scalar(select(AttractionRow).where(AttractionRow.poi_id == poi_id))

    def list_for_city(
        self,
        city_id: int,
        *,
        statuses: set[str] | None = None,
        limit: int = 30,
    ) -> list[AttractionRow]:
        statement = (
            select(AttractionRow)
            .where(AttractionRow.city_id == city_id)
            .order_by(AttractionRow.rating.desc(), AttractionRow.id)
            .limit(limit)
        )
        if statuses:
            statement = statement.where(AttractionRow.knowledge_status.in_(statuses))
        return list(self.session.scalars(statement))

    def count_ready_for_city(self, city_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(AttractionRow)
                .where(
                    AttractionRow.city_id == city_id,
                    AttractionRow.knowledge_status == "ready",
                )
            )
            or 0
        )

    def mark_knowledge_status(
        self,
        poi_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in {"pending", "processing", "ready", "failed"}:
            raise ValueError(f"unsupported knowledge status: {status}")
        attraction = self.find_by_poi_id(poi_id)
        if attraction is None:
            raise LookupError(f"景点 POI 不存在：{poi_id}")
        attraction.knowledge_status = status
        attraction.knowledge_error = error[:500] if error else None
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise



def get_attraction_repository(session: Session = Depends(get_db)) -> AttractionRepository:
    return AttractionRepository(session)
