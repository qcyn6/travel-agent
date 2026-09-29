"""删除全国审计中确认的非景点 POI。

本批仅删除已经核对为车站广场、景区管理机构或明显非景点装置的记录；不改
数据库结构，不调用高德。脚本默认预览，``--commit`` 才会备份并删除，同时
清理对应的本地 Chroma 向量。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


# 34 个车站广场 + 5 个明确的非景点名称。白帝城·瞿塘峡景区虽有模板简介，
# 但它是真实的国家 5A 景区，单独修复而不是删除。
TARGET_IDS = {
    "B0FFFEUIEE", "B03950QA6Q", "B0FFFE0EVK", "B01370WFTU",
    "mct-4a-3564", "B0FFKX46XE", "B015F1828S", "B0K1UMGXVU",
    "B0FFJ12AH8", "B0FFI9QY1J", "B00181BPLQ", "B001817MO6",
    "B00181BPC1", "B0FFIAN47E", "B0FFIPLM5Q", "mct-4a-1820",
    "B0FFFWZB29", "B0FFFSYPRU", "B0FFG28W29", "B0FFF9WF5I",
    "B0FFGZTXKK", "B0FFGYPAGM", "mct-4a-2457", "B01731A02B",
    "B0FFHA1IZI", "B0FFFFZ53A", "B0FFHHRSBU", "B0FFG2MI26",
    "B0FFGA74QE", "B0FFFQ37RK", "B0FFFSFH4Z", "B0HBR76XF3",
    "B0FFFQ6BM0", "mct-4a-571", "B0FFG88KCF", "B0FFKWLVSJ",
    "B0FFGVET68", "B0FFHQNMXK", "B0MAD7J1LA",
}


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .select_from(AttractionRow)
                .join(CityRow, AttractionRow.city_id == CityRow.id)
                .join(ProvinceRow, CityRow.province_id == ProvinceRow.id)
                .where(AttractionRow.poi_id.in_(tuple(TARGET_IDS)))
            ).all()
        )
        found = {row.poi_id: (row, city.name, province.name) for row, city, province in pairs}
        missing = sorted(TARGET_IDS - set(found))
        if missing:
            raise RuntimeError(json.dumps({"missing": missing}, ensure_ascii=False))
        rows = [found[poi_id][0] for poi_id in sorted(TARGET_IDS)]
        affected = [
            {
                "poi_id": row.poi_id,
                "province": found[row.poi_id][2],
                "city": found[row.poi_id][1],
                "name": row.name,
                "reason": "车站广场或明确的管理机构/非景点 POI",
            }
            for row in rows
        ]
        if not commit:
            return {
                "mode": "preview",
                "deletions": len(rows),
                "affected": affected,
                "message": "仅预览；传入 --commit 才删除。",
            }
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"non-attraction-pois-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "reason": "全国审计确认的交通枢纽广场及非景点 POI",
                    "rows": [_payload(row) for row in rows],
                    "affected": affected,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        for row in rows:
            session.delete(row)
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=sorted(TARGET_IDS))
        vectors_deleted = len(TARGET_IDS)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {
        "mode": "commit",
        "deletions": len(rows),
        "backup_path": str(backup_path),
        "vectors_deleted": vectors_deleted,
        "vector_error": vector_error,
        "message": "已删除明确的非景点 POI；未调用高德、未新增字段。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
