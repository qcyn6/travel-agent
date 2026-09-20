from backend.models.attraction import AttractionPOI
from scripts.sync_nationwide_attractions import CityCoverage, _prepare_candidates


def test_prepare_candidates_fills_only_needed_rows_and_derives_metadata():
    coverage = CityCoverage(7, "抚顺市", "210400", "辽宁省", 1)
    items = [
        AttractionPOI(
            poi_id=f"A{index}",
            name=name,
            city="抚顺市",
            category="风景名胜",
            longitude=123.7 + index / 100,
            latitude=41.8 + index / 100,
        )
        for index, name in enumerate(("萨尔浒风景区", "赫图阿拉城", "雷锋纪念馆"), 1)
    ]

    rows = _prepare_candidates(
        coverage,
        items,
        target_per_city=3,
        existing_poi_ids=set(),
        existing_names=set(),
    )

    assert [row.name for row in rows] == ["萨尔浒风景区", "赫图阿拉城"]
    assert all(row.city_id == 7 for row in rows)
    assert all(row.category for row in rows)
    assert all(row.tags for row in rows)
    assert all(row.description for row in rows)
    assert all(row.visit_duration_minutes > 0 for row in rows)


def test_prepare_candidates_skips_existing_id_and_normalized_city_name():
    coverage = CityCoverage(7, "抚顺市", "210400", "辽宁省", 1)
    items = [
        AttractionPOI(poi_id="KNOWN", name="新名字", city="抚顺市"),
        AttractionPOI(poi_id="NEW-1", name="萨尔浒·风景区", city="抚顺市"),
        AttractionPOI(poi_id="NEW-2", name="赫图阿拉城", city="抚顺市"),
    ]

    rows = _prepare_candidates(
        coverage,
        items,
        target_per_city=3,
        existing_poi_ids={"known"},
        existing_names={"萨尔浒风景区"},
    )

    assert [row.poi_id for row in rows] == ["NEW-2"]
