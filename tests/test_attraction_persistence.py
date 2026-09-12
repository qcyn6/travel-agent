import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from backend.attractions.repository import build_attraction_upsert
from backend.database.tables import AttractionKnowledgeRow, AttractionRow
from backend.models.attraction import AttractionPOI
from backend.services import AttractionService


def sample_attraction(name="西湖风景名胜区", rating=4.9):
    return AttractionPOI(
        poi_id="A1",
        name=name,
        city="杭州市",
        category="风景名胜",
        address="西湖区",
        longitude=120.148,
        latitude=30.243,
        rating=rating,
    )


def test_mysql_schema_separates_poi_facts_from_knowledge():
    attraction_sql = str(CreateTable(AttractionRow.__table__).compile(dialect=mysql.dialect()))
    knowledge_sql = str(CreateTable(AttractionKnowledgeRow.__table__).compile(dialect=mysql.dialect()))

    assert "CREATE TABLE attractions" in attraction_sql
    assert "longitude" in attraction_sql
    assert "rating" in attraction_sql
    assert "knowledge_status" in attraction_sql
    assert "CREATE TABLE attraction_knowledge" in knowledge_sql
    assert "FOREIGN KEY(poi_id) REFERENCES attractions (poi_id)" in knowledge_sql
    assert "description" in knowledge_sql
    assert "longitude" not in knowledge_sql


def test_mysql_upsert_updates_mutable_amap_fields():
    statement = build_attraction_upsert(1, [sample_attraction()])
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "rating = VALUES(rating)" in sql
    assert "updated_at = now()" in sql
    assert "first_seen_at" not in sql.split("ON DUPLICATE KEY UPDATE", 1)[1]


@pytest.mark.asyncio
async def test_service_stores_every_returned_attraction():
    items = [sample_attraction(), sample_attraction(name="灵隐寺", rating=4.8).model_copy(update={"poi_id": "A2"})]

    class Provider:
        async def search_attractions(self, city, *, limit):
            assert city == "杭州市"
            assert limit == 30
            return items

    class Repository:
        stored = None

        def upsert_many(self, city_id, attractions):
            self.stored = (city_id, attractions)
            return len(attractions)

    repository = Repository()
    result = await AttractionService(Provider(), repository).search_and_store(
        city_id=1,
        city_name="杭州市",
    )

    assert result == items
    assert repository.stored == (1, items)
