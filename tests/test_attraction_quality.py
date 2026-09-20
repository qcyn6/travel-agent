from types import SimpleNamespace

from backend.attractions.quality import (
    description_origin,
    duplicate_winner_key,
    infer_data_source,
    public_space_kind,
    quality_flags,
)


def row(**updates):
    values = {
        "id": 1,
        "poi_id": "mct-1",
        "name": "古城景区",
        "description": "保留历史文化遗存，适合慢游。",
        "longitude": 120.1,
        "latitude": 30.2,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_source_inference_and_template_detection():
    assert infer_data_source("B123") == "amap"
    assert infer_data_source("mct-123") == "curated"
    assert infer_data_source("demo-hz-1") == "demo"
    assert description_origin("行程中可以围绕自然风光安排游览。") == "template"
    assert description_origin("真实的本地介绍。") == "present"


def test_quality_flags_keep_reviewable_rows_active():
    flags = quality_flags(
        row(
            poi_id="demo-hz-1",
            name="城市公园",
            description="建议预留约 2 小时。",
        )
    )
    assert flags == ["urban_public_space", "template_description", "demo_record"]


def test_non_attraction_and_duplicate_preference():
    assert "non_attraction_name" in quality_flags(row(name="景区游客服务中心"))
    assert public_space_kind("西溪国家湿地公园", "城市公园") == "scenic_park"
    assert public_space_kind("杭州东站东广场", "城市公园") == "facility_square"
    amap = row(id=20, poi_id="B20")
    curated = row(id=10, poi_id="mct-10")
    assert duplicate_winner_key(amap) > duplicate_winner_key(curated)
