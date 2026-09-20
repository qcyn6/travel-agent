from backend.attractions.metadata import (
    city_coordinate_anchor,
    coordinate_for_attraction,
    infer_attraction_category,
    infer_interest_tags,
)


def test_category_rules_prefer_specific_attraction_types():
    assert infer_attraction_category("西溪国家湿地公园") == "生态动植物"
    assert infer_attraction_category("西湖文化广场") == "城市公园"
    assert infer_attraction_category("杭州宋城") == "主题娱乐"
    assert infer_attraction_category("雷峰塔景区") == "历史古迹"
    assert infer_attraction_category("大连市金石滩景区") == "滨海岛屿"
    assert infer_attraction_category("三亚市南山文化旅游区") == "宗教建筑"
    assert infer_attraction_category("三亚市大小洞天旅游区") == "自然山水"
    assert infer_attraction_category("上海城市规划展示馆") == "博物馆与展馆"


def test_tags_keep_core_interests_and_add_specific_signals():
    tags = infer_interest_tags("大理古城", "古镇与街区")

    assert {"古城", "古镇", "历史文化", "人文", "摄影"} <= set(tags)
    assert len(tags) <= 8


def test_tags_do_not_use_city_name_characters_as_nature_signals():
    tags = infer_interest_tags("上海城市规划展示馆", "博物馆与展馆")

    assert {"历史文化", "人文", "博物馆", "研学"} <= set(tags)
    assert "自然" not in tags
    assert "摄影" not in tags


def test_tags_add_specific_water_and_city_walking_signals():
    lake_tags = infer_interest_tags("湘湖国家旅游度假区", "休闲度假")
    street_tags = infer_interest_tags("大唐不夜城", "古镇与街区")

    assert {"自然", "休闲", "湖泊"} <= set(lake_tags)
    assert {"历史文化", "人文", "城市漫游", "夜游"} <= set(street_tags)


def test_missing_coordinates_are_stable_and_use_known_landmark_anchor():
    anchor = city_coordinate_anchor(
        city_name="杭州市",
        province_name="浙江省",
        observed_coordinates=[],
    )
    coordinate = coordinate_for_attraction(
        poi_id="A1",
        name="西湖风景名胜区",
        city_name="杭州市",
        province_name="浙江省",
        anchor=anchor,
    )

    assert coordinate == (120.15, 30.24)
    assert coordinate_for_attraction(
        poi_id="A2",
        name="本地综合景区",
        city_name="杭州市",
        province_name="浙江省",
        anchor=anchor,
    ) == coordinate_for_attraction(
        poi_id="A2",
        name="本地综合景区",
        city_name="杭州市",
        province_name="浙江省",
        anchor=anchor,
    )
