"""Fill cities that currently have no local attraction records.

The project uses a locally maintained demo catalog.  This command adds a
small, deterministic set of attraction records for every city that has zero
rows in ``attractions``.  It does not call Amap or any web source, and it does
not modify existing attraction identities or rows.

Preview is the default.  Pass ``--commit`` to write the rows to MySQL.  The
generated descriptions are intentionally product-oriented demo content: they
provide enough category, interest, coordinate and visit-duration data for the
project's selection, planning and retrieval flows.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.metadata import (  # noqa: E402
    city_coordinate_anchor,
    coordinate_for_attraction,
    infer_attraction_category,
    infer_interest_tags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from scripts.seed_ai_demo_attractions import (  # noqa: E402
    build_description,
    estimate_duration,
)


# Four or five records per uncovered city keeps the nationwide demo catalog
# usable without pretending that every administrative unit has the same
# tourism density.  Names are intentionally stable so rerunning the command
# remains idempotent.
CITY_ATTRACTIONS: dict[str, tuple[str, ...]] = {
    "德宏傣族景颇族自治州": (
        "勐焕大金塔",
        "一寨两国",
        "瑞丽江—大盈江风景区",
        "姐告口岸边贸街区",
        "勐巴娜西珍奇园",
    ),
    "怒江傈僳族自治州": (
        "怒江大峡谷",
        "丙中洛风景区",
        "独龙江生态体验区",
        "石月亮观景台",
        "老姆登村",
    ),
    "张家口市": (
        "草原天路",
        "崇礼滑雪旅游度假区",
        "大境门景区",
        "鸡鸣山风景区",
        "黄帝城遗址文化旅游区",
    ),
    "丹东市": (
        "鸭绿江断桥",
        "虎山长城",
        "凤凰山风景区",
        "天桥沟森林公园",
        "抗美援朝纪念馆",
    ),
    "阜新市": (
        "海棠山风景区",
        "瑞应寺",
        "乌兰木图山",
        "阜新万人坑遗址",
        "蒙古贞文化产业园",
    ),
    "葫芦岛市": (
        "兴城古城",
        "觉华岛",
        "九门口长城",
        "龙潭大峡谷",
        "葫芦山庄",
    ),
    "七台河市": (
        "桃山公园",
        "石龙山国家森林公园",
        "桃山湖生态景区",
        "勃利青山国家森林公园",
        "七台河市博物馆",
    ),
    "新余市": (
        "仙女湖风景区",
        "抱石公园",
        "仰天岗森林公园",
        "罗坊会议红色景区",
        "宽心小镇",
    ),
    "仙桃市": (
        "沔阳小镇",
        "梦里水乡文化旅游区",
        "陈友谅故居",
        "沔城遗址",
        "仙桃排湖风景区",
    ),
    "潜江市": (
        "曹禺文化旅游区",
        "潜江龙虾小镇",
        "返湾湖风景区",
        "章华台遗址",
        "兴隆水利枢纽生态园",
    ),
    "天门市": (
        "茶圣故里园",
        "石家河遗址",
        "陆羽纪念馆",
        "胡家花园",
        "天门东湖公园",
    ),
    "三沙市": (
        "永兴岛海岛风光",
        "西沙海洋博物馆",
        "鸭公岛生态体验区",
        "全富岛海岸景观",
        "西沙群岛海洋观景带",
    ),
    "儋州市": (
        "东坡书院",
        "千年古盐田",
        "石花水洞地质公园",
        "蓝洋温泉度假区",
        "峨蔓火山海岸",
    ),
    "五指山市": (
        "五指山热带雨林风景区",
        "海南民族博物馆",
        "五指山革命根据地纪念园",
        "黎峒文化园",
        "牙胡梯田",
    ),
    "文昌市": (
        "文昌航天发射场",
        "东郊椰林",
        "铜鼓岭景区",
        "文昌孔庙",
        "月亮湾海岸",
    ),
    "东方市": (
        "鱼鳞洲风景区",
        "俄贤岭生态旅游区",
        "白查村船形屋",
        "大广坝水库景区",
        "东方滨海休闲带",
    ),
    "屯昌县": (
        "木色旅游度假区",
        "梦幻香山芳香文化园",
        "加乐潭生态旅游区",
        "屯昌油画步行街",
        "南坤民族风情村",
    ),
    "澄迈县": (
        "福山咖啡文化风情镇",
        "永庆寺",
        "美榔双塔",
        "红树林湿地公园",
        "盈滨海岸",
    ),
    "临高县": (
        "临高角解放公园",
        "文澜文化公园",
        "百仞滩瀑布",
        "临高灯塔海岸",
        "金沙湾滨海旅游区",
    ),
    "白沙黎族自治县": (
        "邦溪自然保护区",
        "白沙起义纪念园",
        "红坎瀑布",
        "白沙茶园观光区",
        "南开河生态景观带",
    ),
    "昌江黎族自治县": (
        "棋子湾旅游度假区",
        "霸王岭国家森林公园",
        "昌化古城",
        "石碌铁矿工业遗产园",
        "海尾湿地公园",
    ),
    "乐东黎族自治县": (
        "尖峰岭国家森林公园",
        "莺歌海盐场",
        "龙沐湾滨海旅游区",
        "毛公山景区",
        "黄流老街",
    ),
    "琼中黎族苗族自治县": (
        "黎母山国家森林公园",
        "百花岭热带雨林文化旅游区",
        "什寒村",
        "鹦哥岭自然保护区",
        "琼中湾岭茶园",
    ),
    "黔西南布依族苗族自治州": (
        "万峰林景区",
        "马岭河峡谷",
        "万峰湖风景区",
        "双乳峰景区",
        "布依族村寨文化体验区",
    ),
    "黔东南苗族侗族自治州": (
        "西江千户苗寨",
        "镇远古城",
        "肇兴侗寨",
        "岜沙苗寨",
        "隆里古城",
    ),
    "黔南布依族苗族自治州": (
        "荔波小七孔景区",
        "荔波大七孔景区",
        "中国天眼景区",
        "茂兰国家级自然保护区",
        "都匀秦汉影视城",
    ),
    "那曲市": (
        "纳木错北岸景区",
        "萨普神山",
        "羌塘国家级自然保护区",
        "比如骷髅墙",
        "那曲草原观景区",
    ),
    "阿里地区": (
        "古格王朝遗址",
        "札达土林",
        "班公湖",
        "玛旁雍错",
        "冈仁波齐景区",
    ),
    "果洛藏族自治州": (
        "年保玉则生态景区",
        "阿尼玛卿山",
        "黄河源头景观区",
        "班玛原始森林",
        "格萨尔林卡文化园",
    ),
    "玉树藏族自治州": (
        "三江源国家公园玉树片区",
        "文成公主庙",
        "新寨嘉那嘛呢石经城",
        "巴塘草原",
        "勒巴沟岩画",
    ),
    "阿拉尔市": (
        "塔里木河生态旅游区",
        "三五九旅屯垦纪念馆",
        "阿拉尔国家湿地公园",
        "沙漠之门景区",
        "塔克拉玛干沙漠边缘观景区",
    ),
    "图木舒克市": (
        "唐王城遗址",
        "图木舒克湿地公园",
        "丝路屯垦文化园",
        "喀什河生态景观带",
        "图木舒克城市规划展示馆",
    ),
    "北屯市": (
        "乌伦古湖黄金海岸",
        "额尔齐斯河国家湿地公园",
        "北屯市滨河公园",
        "乌伦古湖渔村",
        "北屯兵团文化展馆",
    ),
    "铁门关市": (
        "铁门关景区",
        "孔雀河生态景观带",
        "铁门关兵团文化公园",
        "古丝路关隘体验区",
        "铁门关城市展馆",
    ),
    "可克达拉市": (
        "可克达拉草原",
        "伊犁河谷生态观景带",
        "紫色花海景区",
        "惠远古城文化体验区",
        "可克达拉城市公园",
    ),
    "昆玉市": (
        "昆玉湖生态景区",
        "玉泉山景区",
        "兵团屯垦文化园",
        "和田河生态景观带",
        "昆玉市博物馆",
    ),
    "胡杨河市": (
        "胡杨河胡杨林",
        "奎屯河生态公园",
        "兵团农垦博物馆",
        "胡杨河田园观光区",
        "城市绿洲休闲带",
    ),
    "新星市": (
        "东天山景观带",
        "新星市田园观光区",
        "兵团屯垦文化园",
        "星星峡丝路驿站",
        "新星市城市公园",
    ),
    "白杨市": (
        "白杨市边境草原",
        "塔城河生态景观带",
        "兵团文化展馆",
        "额敏河湿地观景区",
        "白杨市田园休闲区",
    ),
}


# ``metadata.city_coordinate_anchor`` intentionally only carries the anchors
# already used by the older catalog.  These are the approximate city/prefecture
# centers needed by the newly covered cities; they keep route demonstrations
# near the selected destination instead of falling back to a province center.
CITY_ANCHORS: dict[str, tuple[float, float]] = {
    "德宏傣族景颇族自治州": (98.58, 24.44),
    "怒江傈僳族自治州": (98.86, 25.82),
    "张家口市": (114.89, 40.82),
    "丹东市": (124.35, 40.13),
    "阜新市": (121.67, 42.02),
    "葫芦岛市": (120.84, 40.71),
    "七台河市": (131.00, 45.77),
    "新余市": (114.92, 27.82),
    "仙桃市": (113.45, 30.36),
    "潜江市": (112.90, 30.42),
    "天门市": (113.17, 30.66),
    "三沙市": (112.34, 16.83),
    "儋州市": (109.58, 19.52),
    "五指山市": (109.52, 18.78),
    "文昌市": (110.75, 19.61),
    "东方市": (108.65, 19.10),
    "屯昌县": (110.10, 19.35),
    "澄迈县": (110.00, 19.74),
    "临高县": (109.69, 19.91),
    "白沙黎族自治县": (109.44, 19.22),
    "昌江黎族自治县": (109.05, 19.26),
    "乐东黎族自治县": (109.17, 18.75),
    "琼中黎族苗族自治县": (109.84, 19.03),
    "黔西南布依族苗族自治州": (104.91, 25.09),
    "黔东南苗族侗族自治州": (107.98, 26.58),
    "黔南布依族苗族自治州": (107.52, 26.26),
    "那曲市": (92.05, 31.48),
    "阿里地区": (80.10, 32.50),
    "果洛藏族自治州": (100.24, 34.47),
    "玉树藏族自治州": (97.01, 33.01),
    "阿拉尔市": (81.28, 40.55),
    "图木舒克市": (79.13, 39.87),
    "北屯市": (87.82, 47.33),
    "铁门关市": (86.22, 41.85),
    "可克达拉市": (80.99, 43.94),
    "昆玉市": (79.27, 37.21),
    "胡杨河市": (84.83, 44.69),
    "新星市": (93.51, 42.82),
    "白杨市": (82.98, 46.75),
}


def _is_zero_city(session, city_id: int) -> bool:
    count = session.scalar(
        select(func.count(AttractionRow.id)).where(AttractionRow.city_id == city_id)
    )
    return int(count or 0) == 0


def _serialise(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    return value


def _build_rows(city: CityRow, province: ProvinceRow, names: tuple[str, ...]):
    anchor = CITY_ANCHORS.get(city.name) or city_coordinate_anchor(
        city_name=city.name,
        province_name=province.name,
        observed_coordinates=[],
    )
    attractions: list[AttractionRow] = []
    for index, name in enumerate(names, start=1):
        poi_id = f"demo-{city.adcode}-{index:02d}"
        category = infer_attraction_category(name)
        tags = infer_interest_tags(name, category)
        duration = estimate_duration(category, tags, name)
        longitude, latitude = coordinate_for_attraction(
            poi_id=poi_id,
            name=name,
            city_name=city.name,
            province_name=province.name,
            anchor=anchor,
        )
        attractions.append(
            AttractionRow(
                poi_id=poi_id,
                city_id=city.id,
                name=name,
                category=category,
                tags=tags,
                description=build_description(
                    city=city.name,
                    name=name,
                    category=category,
                    tags=tags,
                    duration=duration,
                ),
                longitude=longitude,
                latitude=latitude,
                visit_duration_minutes=duration,
            )
        )
    return attractions


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    args = parser.parse_args()

    init_database()
    with open_session() as session:
        rows = list(
            session.execute(
                select(CityRow, ProvinceRow)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .order_by(CityRow.id)
            ).all()
        )
        selected: list[tuple[CityRow, ProvinceRow, tuple[str, ...]]] = []
        fallback_cities: list[str] = []
        for city, province in rows:
            if not _is_zero_city(session, city.id):
                continue
            names = CITY_ATTRACTIONS.get(city.name)
            if names is None:
                fallback_cities.append(city.name)
                names = (
                    f"{city.name}自然风光体验区",
                    f"{city.name}历史文化街区",
                    f"{city.name}城市公园",
                    f"{city.name}地方文化展馆",
                )
            selected.append((city, province, names))

        preview = []
        category_distribution: Counter[str] = Counter()
        attraction_count = 0
        all_attractions: list[AttractionRow] = []
        for city, province, names in selected:
            attractions = _build_rows(city, province, names)
            all_attractions.extend(attractions)
            attraction_count += len(attractions)
            category_distribution.update(row.category for row in attractions)
            if len(preview) < 20:
                preview.extend(
                    {
                        "city": city.name,
                        "province": province.name,
                        "poi_id": row.poi_id,
                        "name": row.name,
                        "category": row.category,
                        "tags": row.tags,
                        "longitude": row.longitude,
                        "latitude": row.latitude,
                    }
                    for row in attractions
                )
                preview = preview[:20]

        if args.commit and all_attractions:
            session.add_all(all_attractions)
            session.commit()

        result = {
            "zero_cities_selected": len(selected),
            "attractions_to_add": attraction_count,
            "durations_to_add": len(all_attractions),
            "committed": args.commit,
            "fallback_cities": fallback_cities,
            "category_distribution": dict(sorted(category_distribution.items())),
            "preview": preview,
        }
        print(json.dumps(_serialise(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
