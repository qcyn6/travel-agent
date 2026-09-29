"""修复审计发现的非标准标签残留。默认预览。"""

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
from backend.database.tables import AttractionRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


TARGET_ID = "verified-hb-zhangjiakou-chongli-ski-resort"
NEW_TAGS = ["休闲", "冰雪", "自然", "摄影"]


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def repair(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        row = session.scalar(select(AttractionRow).where(AttractionRow.poi_id == TARGET_ID))
        if row is None:
            raise RuntimeError(f"未找到 {TARGET_ID}")
        before = _payload(row)
        preview = {"poi_id": row.poi_id, "name": row.name, "before_tags": list(row.tags or []), "after_tags": NEW_TAGS}
        if not commit:
            return {"mode": "preview", **preview, "message": "仅预览；传入 --commit 才写入。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"residual-tag-vocab-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "row": before, "after_tags": NEW_TAGS}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        row.tags = NEW_TAGS
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=[TARGET_ID])
        vectors_deleted = 1
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"mode": "commit", **preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已修复标签词表残留；未调用高德、未新增字段。"}


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
