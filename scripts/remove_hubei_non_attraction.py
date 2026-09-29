"""删除湖北明确的交通枢纽非景点记录；默认预览。"""

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


TARGET = {"B0HGFG3EDJ": {"city": "荆州市", "name": "荆州站北广场", "reason": "交通枢纽广场，不是独立旅游景点。"}}


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        pairs = list(session.execute(select(AttractionRow, CityRow).join(CityRow).join(ProvinceRow, ProvinceRow.id == CityRow.province_id).where(ProvinceRow.name == "湖北省", AttractionRow.poi_id.in_(tuple(TARGET)))).all())
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(TARGET) - set(found))
        mismatches = []
        for poi_id, spec in TARGET.items():
            if poi_id in found and (found[poi_id][0].name != spec["name"] or found[poi_id][1] != spec["city"]):
                mismatches.append({"poi_id": poi_id, "expected": f"{spec['city']} / {spec['name']}", "actual": f"{found[poi_id][1]} / {found[poi_id][0].name}"})
        if missing or mismatches:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches}, ensure_ascii=False))
        rows = [found[poi_id][0] for poi_id in TARGET]
        preview = [{"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "reason": TARGET[row.poi_id]["reason"]} for row in rows]
        if not commit:
            return {"province": "湖北省", "mode": "preview", "deletions": len(rows), "affected": preview, "message": "仅预览；传入 --commit 才删除。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"hubei-non-attraction-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": "湖北省", "reason": "remove-transport-hub-square", "rows": [_payload(row) for row in rows], "decisions": TARGET}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for row in rows:
            session.delete(row)
        session.commit()
    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=list(TARGET))
        vectors_deleted = len(TARGET)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": "湖北省", "mode": "commit", "deletions": len(rows), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已删除湖北交通枢纽广场非景点记录；未调用高德、未新增字段。"}


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
