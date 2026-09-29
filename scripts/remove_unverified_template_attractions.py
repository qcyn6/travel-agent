"""Remove attractions whose descriptions are still generated templates.

This is the final safety pass for the public-description cleanup.  The user
previously chose “找不到真实数据就删除”; this script applies that rule only to
rows that still contain the unmistakable old planning-template marker.  It
backs up complete rows first, then removes matching Chroma vectors.  No Amap
calls and no schema changes are involved.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


def _payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_database()
    with open_session() as session:
        rows = list(
            session.scalars(
                select(AttractionRow)
                .where(AttractionRow.description.like("%规划时长按%"))
                .order_by(AttractionRow.id)
            )
        )
        summary = [{"poi_id": row.poi_id, "name": row.name} for row in rows]
        if not args.commit:
            print(json.dumps({"mode": "preview", "remove": len(rows), "sample": summary[:30], "message": "仅匹配旧模板；传入 --commit 才删除。"}, ensure_ascii=False, indent=2))
            return
        backup_dir = ROOT / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"unverified-template-attractions-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "reason": "未找到可核验公开描述；按用户既定规则删除", "rows": [_payload(row) for row in rows]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        poi_ids = [row.poi_id for row in rows]
        for row in rows:
            session.delete(row)
        session.commit()
    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        if poi_ids:
            index.collection.delete(ids=poi_ids)
        vectors_deleted = len(poi_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    print(json.dumps({"mode": "commit", "removed": len(poi_ids), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已删除未核验模板景点及其向量；未调用高德、未新增字段。"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
