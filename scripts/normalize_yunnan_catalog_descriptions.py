"""Normalize map-style short descriptions in the Yunnan catalog.

Some imported rows only contain the provider display name, category, and
address (for example ``名称；类别：自然山水；地址：……``).  Those strings are
useful as an import trace but are poor retrieval text and should not be
mistaken for an official introduction.  This command replaces only that
strict map-style shape with a transparent planning description built from
fields that already exist in the attraction table.

The command never calls Amap or another external service and never changes
the attraction schema.  Preview is the default; ``--commit`` writes a full
backup before updating ``description`` for the selected Yunnan rows.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402


_MAP_STYLE = re.compile(r"^.+；类别：[^；]+(?:；地址：.*)?$")
_FACTUAL_MARKERS = ("咨询电话：", "开放信息：", "开放时间：")


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-description-normalize-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-description-normalize-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _focus(tags: Sequence[str] | None, category: str | None) -> str:
    clean = {str(tag).strip() for tag in tags or [] if str(tag).strip()}
    if clean.intersection({"历史文化", "人文", "古镇", "古城", "古建", "宗教文化"}):
        return "历史文化观察、建筑欣赏和城市漫游"
    if clean.intersection({"自然", "山地", "森林", "湿地", "湖泊", "江河", "高原", "地质景观"}):
        return "自然观赏、户外步行和摄影"
    if clean.intersection({"民族文化", "民俗", "乡村", "茶文化"}):
        return "民族文化体验、乡村漫游和摄影"
    if clean.intersection({"亲子", "主题娱乐", "花卉"}):
        return "主题体验、亲子活动和轻松游览"
    if clean.intersection({"休闲", "城市漫游", "夜游", "美食"}):
        return "城市休闲、街区探索和短时游览"
    return f"{str(category or '景点').strip() or '景点'}主题体验和灵活游览"


def _address(description: str) -> str | None:
    match = re.search(r"；地址：(.+)$", description)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def build_description(row: AttractionRow, city_name: str) -> str:
    tags = [str(tag).strip() for tag in row.tags or [] if str(tag).strip()]
    tag_text = "、".join(tags[:6]) or str(row.category or "景点")
    location = _address(row.description or "")
    location_text = f"当前目录位置登记为{location}。" if location else "当前目录未登记更细的位置说明。"
    return (
        f"{city_name}的{row.name}归入“{row.category or '景点'}”，现有兴趣标签为{tag_text}。"
        f"适合{_focus(row.tags, row.category)}；{location_text}"
        f"规划时长按{int(row.visit_duration_minutes or 90)}分钟估算，"
        "开放、预约、交通和收费请以出行前的实时公开信息为准。"
    )


def _selected(row: AttractionRow) -> bool:
    text = str(row.description or "").strip()
    # Keep the already-curated government-list descriptions with phone and
    # opening information; only normalize the strict provider-shaped text.
    return bool(_MAP_STYLE.fullmatch(text)) and not any(
        marker in text for marker in _FACTUAL_MARKERS
    )


def normalize(*, commit: bool, backup_dir: Path, sample_limit: int) -> dict:
    init_database()
    with open_session() as session:
        province = session.scalar(select(ProvinceRow).where(ProvinceRow.name == "云南省"))
        if province is None:
            raise RuntimeError("数据库中没有找到云南省")
        rows = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .where(CityRow.province_id == province.id)
                .order_by(AttractionRow.id)
            ).all()
        )
        updates = []
        for row, city in rows:
            if not _selected(row):
                continue
            updates.append(
                {
                    "row": row,
                    "city": city.name,
                    "poi_id": row.poi_id,
                    "name": row.name,
                    "before": row.description,
                    "after": build_description(row, city.name),
                }
            )

        backup = None
        if commit and updates:
            backup = _backup_path(backup_dir)
            backup.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "province": "云南省",
                        "selected": len(updates),
                        "rows": [_row_payload(row) for row in session.scalars(select(AttractionRow))],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            for item in updates:
                item["row"].description = item["after"]
            session.commit()

        return {
            "province": "云南省",
            "selected": len(updates),
            "updated": len(updates) if commit else 0,
            "committed": commit,
            "backup_path": str(backup) if backup else None,
            "remaining_map_style": 0 if commit else len(updates),
            "preview": [
                {key: item[key] for key in ("city", "poi_id", "name", "before", "after")}
                for item in updates[:sample_limit]
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("sample-limit 不能小于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(normalize(commit=args.commit, backup_dir=args.backup_dir, sample_limit=args.sample_limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
