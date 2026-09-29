"""修复重庆白帝城·瞿塘峡景区的真实简介。

该景区是真实的国家 5A 景区，不能因导入时的模板简介而删除。本脚本仅更新
景点表已有字段，默认预览；``--commit`` 才写入并备份。全程不调用高德。
公开核验：
https://www.bdcqtx.com/jqjs/index.jhtml
https://cqfj.gov.cn/fjwl/jpjq/202209/t20220914_11107444.html
https://cq.gov.cn/zjcq/cycq/zmjd/zqajjq/
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


POI_ID = "mct-5a-35044"
EXPECTED_NAME = "白帝城•瞿塘峡景区"
NEW_VALUES = {
    "category": "历史古迹",
    "tags": ["历史文化", "人文", "滨水", "摄影"],
    "visit_duration_minutes": 240,
    "description": (
        "白帝城·瞿塘峡景区位于重庆市奉节县瞿塘峡口长江北岸白帝山，地处长江三峡西入口，"
        "由白帝城和瞿塘峡两部分组成，融三峡峡谷自然景观、古城遗址、诗歌文化与历史遗迹于一体，"
        "是国家5A级旅游景区。适合安排白帝城古迹、夔门峡景与诗歌人文体验；开放时间、限流和预约"
        "以景区官方公告为准。"
    ),
}
SOURCE_URLS = [
    "https://www.bdcqtx.com/jqjs/index.jhtml",
    "https://cqfj.gov.cn/fjwl/jpjq/202209/t20220914_11107444.html",
    "https://cq.gov.cn/zjcq/cycq/zmjd/zqajjq/",
]


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def repair(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        result = session.execute(
            select(AttractionRow, CityRow, ProvinceRow)
            .select_from(AttractionRow)
            .join(CityRow, AttractionRow.city_id == CityRow.id)
            .join(ProvinceRow, CityRow.province_id == ProvinceRow.id)
            .where(AttractionRow.poi_id == POI_ID)
        ).one_or_none()
        if result is None:
            raise RuntimeError(f"未找到 {POI_ID}")
        row, city, province = result
        if row.name != EXPECTED_NAME or province.name != "重庆市" or city.name != "重庆市":
            raise RuntimeError(f"记录不匹配：{province.name}/{city.name}/{row.name}")
        before = _payload(row)
        preview = {
            "poi_id": row.poi_id,
            "province": province.name,
            "city": city.name,
            "name": row.name,
            "before": before,
            "after": NEW_VALUES,
            "sources": SOURCE_URLS,
        }
        if not commit:
            return {"mode": "preview", **preview, "message": "仅预览；传入 --commit 才写入。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"chongqing-baidi-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "source_urls": SOURCE_URLS,
                    "row": before,
                    "new_values": NEW_VALUES,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        for key, value in NEW_VALUES.items():
            setattr(row, key, value)
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=[POI_ID])
        vectors_deleted = 1
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {
        "mode": "commit",
        **preview,
        "backup_path": str(backup_path),
        "vectors_deleted": vectors_deleted,
        "vector_error": vector_error,
        "message": "已修复真实景区简介和规划字段；未调用高德、未新增字段。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(repair(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
