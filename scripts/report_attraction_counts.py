"""Generate a current province/city attraction-count Markdown report."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402


OUT = ROOT / "docs" / "景点数量统计-当前.md"


def main() -> None:
    init_database()
    with open_session() as session:
        provinces = list(session.scalars(select(ProvinceRow).order_by(ProvinceRow.name)))
        cities = list(
            session.execute(
                select(CityRow.id, CityRow.province_id, CityRow.name, func.count(AttractionRow.id))
                .outerjoin(AttractionRow, AttractionRow.city_id == CityRow.id)
                .group_by(CityRow.id, CityRow.province_id, CityRow.name)
                .order_by(CityRow.province_id, CityRow.name)
            ).all()
        )
        attraction_count = session.scalar(select(func.count()).select_from(AttractionRow)) or 0
        empty_description = session.scalar(
            select(func.count())
            .select_from(AttractionRow)
            .where((AttractionRow.description.is_(None)) | (AttractionRow.description == ""))
        ) or 0

    by_province: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for _city_id, province_id, city_name, count in cities:
        by_province[province_id].append((city_name, int(count)))
    summaries = []
    for province in provinces:
        items = by_province[province.id]
        summaries.append(
            {
                "name": province.name,
                "cities": len(items),
                "attractions": sum(count for _name, count in items),
                "zero": sum(count == 0 for _name, count in items),
                "items": sorted(items, key=lambda item: (-item[1], item[0])),
            }
        )
    summaries.sort(key=lambda item: (-item["attractions"], item["name"]))

    lines = [
        "# 当前景点数量统计",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（本地数据库实时汇总）",
        "",
        f"- 景点总数：**{attraction_count}**",
        f"- 省级行政区记录：**{len(provinces)}**",
        f"- 城市/地区记录：**{len(cities)}**",
        f"- 空描述景点：**{empty_description}**",
        "- 统计口径：仅统计当前 `attractions` 表中仍保留的景点；城市 0 条不代表城市记录被删除。",
        "",
        "## 省份汇总",
        "",
        "|排名|省份/地区|城市数|景点数|0 景点城市数|",
        "|---:|---|---:|---:|---:|",
    ]
    for rank, item in enumerate(summaries, 1):
        lines.append(f"|{rank}|{item['name']}|{item['cities']}|{item['attractions']}|{item['zero']}|")
    lines.extend(["", "## 城市明细", ""])
    for item in summaries:
        lines.extend([f"### {item['name']}（{item['attractions']} 个景点，{item['cities']} 个城市/地区）", "", "|城市/地区|景点数|", "|---|---:|"])
        for city_name, count in item["items"]:
            lines.append(f"|{city_name}|{count}|")
        lines.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
