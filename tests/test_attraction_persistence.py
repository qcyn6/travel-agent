import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from backend.attractions.repository import build_attraction_upsert
from backend.database.session import Base
from backend.database.tables import AttractionRow
from backend.models.attraction import AttractionPOI
from backend.services import AttractionService


def sample_attraction(name="西湖风景名胜区", rating=4.9):
    return AttractionPOI(
        poi_id="A1",
        name=name,
        city="杭州市",
        category="风景名胜",
        tags=["自然", "摄影"],
        description="适合自然观赏与摄影的本地演示景点描述。",
        address="西湖区",  # external Amap response compatibility only
        longitude=120.148,
        latitude=30.243,
        rating=rating,
    )


def test_mysql_schema_keeps_only_planning_fields_on_attraction():
    attraction_sql = str(CreateTable(AttractionRow.__table__).compile(dialect=mysql.dialect()))

    assert "CREATE TABLE attractions" in attraction_sql
    assert "longitude" in attraction_sql
    assert "latitude" in attraction_sql
    assert "tags" in attraction_sql
    assert "description" in attraction_sql
    assert "visit_duration_minutes" in attraction_sql
    assert "data_source" in attraction_sql
    assert "description_origin" in attraction_sql
    assert "duration_origin" in attraction_sql
    assert "quality_status" in attraction_sql
    assert "quality_flags" in attraction_sql
    assert "is_active" in attraction_sql
    for removed in (
        "interest_tags",
        "address",
        "rating",
        "knowledge_status",
        "knowledge_error",
        "knowledge_quality",
        "tourism_level",
        "core_scope",
        "environment",
        "first_seen_at",
        "updated_at",
    ):
        assert removed not in attraction_sql
    assert "attraction_knowledge" not in Base.metadata.tables


def test_mysql_upsert_updates_provider_fields_without_erasing_local_content():
    statement = build_attraction_upsert(1, [sample_attraction()])
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "city_id = VALUES(city_id)" in sql
    assert "longitude = VALUES(longitude)" in sql
    assert "latitude = VALUES(latitude)" in sql
    assert "visit_duration_minutes" in sql
    update_sql = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "tags = VALUES(tags)" not in update_sql
    assert "description = VALUES(description)" not in update_sql
    for removed in (
        "rating",
        "address",
        "knowledge_status",
        "first_seen_at",
        "updated_at",
        "attraction_facts",
    ):
        assert removed not in sql


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


@pytest.mark.asyncio
async def test_service_deduplicates_same_attraction_before_storage():
    first = sample_attraction(name="白帝城·瞿塘峡景区")
    duplicate_name = sample_attraction(name="白帝城•瞿塘峡景区").model_copy(
        update={"poi_id": "A2"}
    )

    class Provider:
        async def search_attractions(self, city, *, limit):
            return [first, duplicate_name]

    class Repository:
        stored = None

        def upsert_many(self, city_id, attractions):
            self.stored = (city_id, attractions)
            return len(attractions)

    repository = Repository()
    result = await AttractionService(Provider(), repository).search_and_store(
        city_id=1,
        city_name="重庆市",
    )

    assert [item.name for item in result] == ["白帝城·瞿塘峡景区"]
    assert [item.name for item in repository.stored[1]] == ["白帝城·瞿塘峡景区"]
