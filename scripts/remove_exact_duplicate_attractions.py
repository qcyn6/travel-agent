"""删除已经确认的同城同名重复景点。

每组保留前一轮公开网页核验过的 ``verified-*`` 记录，删除同名 Amap 导入
记录。默认预览，``--commit`` 才备份并删除；不调用高德、不新增字段。
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


TARGET = {
    "B0J2FM3FQO": {"province": "新疆维吾尔自治区", "city": "阿拉尔市", "name": "塔河源景区", "keep": "verified-xj-alar-tahe-source"},
    "B031600HFV": {"province": "江西省", "city": "新余市", "name": "仙女湖风景名胜区", "keep": "verified-jx-xinyu-fairy-lake"},
    "B0FFFEVN0C": {"province": "海南省", "city": "澄迈县", "name": "海南红树湾湿地公园", "keep": "verified-hn-chengmai-mangrove"},
    "B0FFI33JEW": {"province": "西藏自治区", "city": "阿里地区", "name": "玛旁雍措", "keep": "verified-xz-ali-mapam-yumco"},
    "B01AD00BQT": {"province": "辽宁省", "city": "葫芦岛市", "name": "龙潭大峡谷自然风景区", "keep": "verified-ln-huludao-longtan-canyon"},
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
                .where(AttractionRow.poi_id.in_(tuple(TARGET)))
            ).all()
        )
        found = {row.poi_id: (row, city.name, province.name) for row, city, province in pairs}
        missing = sorted(set(TARGET) - set(found))
        if missing:
            raise RuntimeError(json.dumps({"missing": missing}, ensure_ascii=False))
        mismatches = []
        for poi_id, spec in TARGET.items():
            row, city, province = found[poi_id]
            if (city, province, row.name) != (spec["city"], spec["province"], spec["name"]):
                mismatches.append({"poi_id": poi_id, "expected": spec, "actual": {"city": city, "province": province, "name": row.name}})
        if mismatches:
            raise RuntimeError(json.dumps({"mismatches": mismatches}, ensure_ascii=False))
        rows = [found[poi_id][0] for poi_id in sorted(TARGET)]
        affected = [
            {"poi_id": row.poi_id, "province": found[row.poi_id][2], "city": found[row.poi_id][1], "name": row.name, "kept": TARGET[row.poi_id]["keep"], "reason": "同城同名，保留已公开核验记录"}
            for row in rows
        ]
        if not commit:
            return {"mode": "preview", "deletions": len(rows), "affected": affected, "message": "仅预览；传入 --commit 才删除。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"exact-duplicates-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "rows": [_payload(row) for row in rows], "affected": affected}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for row in rows:
            session.delete(row)
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=sorted(TARGET))
        vectors_deleted = len(TARGET)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"mode": "commit", "deletions": len(rows), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已删除同城同名重复记录；未调用高德、未新增字段。"}


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
