"""把云南残留的合成 demo 记录迁移/清理为可审计的公开核验记录。

本脚本不调用高德或任何地图 API，也不新增数据库字段：

* 4 条有公开资料支撑的区域性目的地改用项目内部的 ``verified-yn-*``
  稳定标识，保留原有规划字段；
* ``老姆登村`` 的 demo 行因已有真实景点行 ``B0H2L76OAR`` 而删除；
* 昆明滇池岸线的两个古渔村从异常的“滨海岛屿”修正为“滨水景观”。

默认只预览；显式传入 ``--commit`` 才会备份并写入数据库。
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


YUNNAN = "云南省"

# These are internal canonical identities for publicly verified records. They
# are intentionally not provider IDs: no Amap quota was used and no provider
# identity is being invented.
DEMO_REPLACEMENTS: dict[str, dict[str, str]] = {
    "demo-533100-03": {
        "name": "瑞丽江—大盈江风景区",
        "new_poi_id": "verified-yn-rldjdyj",
    },
    "demo-533100-04": {
        "name": "姐告口岸边贸街区",
        "new_poi_id": "verified-yn-jiegao",
    },
    "demo-533300-01": {
        "name": "怒江大峡谷",
        "new_poi_id": "verified-yn-nujiang-canyon",
    },
    "demo-533300-03": {
        "name": "独龙江生态体验区",
        "new_poi_id": "verified-yn-dulongjiang",
    },
}

DUPLICATE_DEMO = {
    "poi_id": "demo-533300-05",
    "name": "老姆登村",
    "real_poi_id": "B0H2L76OAR",
    "real_name": "老姆登村景区",
}

COASTAL_FIXES: dict[str, dict[str, Any]] = {
    "B0G0DS8H7L": {
        "name": "昆明卧龙古渔村",
        "category": "滨水景观",
        "tags": ["湖泊", "乡村", "历史文化", "休闲", "摄影"],
        "duration": 120,
        "description": "昆明卧龙古渔村位于滇池湖岸，以传统渔村空间、村落建筑和湖滨景观为主要体验。适合乡村漫游、湖畔摄影和地方文化观察，属于滇池滨水目的地，不属于海滨或海岛景点。",
    },
    "B0G2AK3KPJ": {
        "name": "海晏古渔村",
        "category": "滨水景观",
        "tags": ["湖泊", "乡村", "历史文化", "休闲", "摄影"],
        "duration": 120,
        "description": "海晏古渔村位于昆明滇池东岸，保留传统村落空间、古渔村生活记忆和湖滨日落景观。适合村落漫游、湖景摄影和近郊休闲，分类按滇池滨水景观处理。",
    },
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-demo-resolution-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-demo-resolution-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def resolve(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(DEMO_REPLACEMENTS) + (DUPLICATE_DEMO["poi_id"],) + tuple(COASTAL_FIXES)
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == YUNNAN, AttractionRow.poi_id.in_(target_ids))
                .order_by(AttractionRow.id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}

        required = set(DEMO_REPLACEMENTS) | {DUPLICATE_DEMO["poi_id"]} | set(COASTAL_FIXES)
        missing = sorted(required - set(found))
        mismatches = []
        for poi_id, spec in DEMO_REPLACEMENTS.items():
            if poi_id in found and found[poi_id][0].name != spec["name"]:
                mismatches.append({"poi_id": poi_id, "expected": spec["name"], "actual": found[poi_id][0].name})
        if DUPLICATE_DEMO["poi_id"] in found and found[DUPLICATE_DEMO["poi_id"]][0].name != DUPLICATE_DEMO["name"]:
            mismatches.append({"poi_id": DUPLICATE_DEMO["poi_id"], "expected": DUPLICATE_DEMO["name"], "actual": found[DUPLICATE_DEMO["poi_id"]][0].name})
        for poi_id, spec in COASTAL_FIXES.items():
            if poi_id in found and (found[poi_id][0].name != spec["name"] or found[poi_id][0].category != "滨海岛屿"):
                mismatches.append({"poi_id": poi_id, "expected": f"{spec['name']} / 滨海岛屿", "actual": f"{found[poi_id][0].name} / {found[poi_id][0].category}"})

        new_ids = {spec["new_poi_id"] for spec in DEMO_REPLACEMENTS.values()}
        conflicting = [row.poi_id for row in session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(tuple(new_ids))))]
        real_duplicate = session.scalar(select(AttractionRow).where(AttractionRow.poi_id == DUPLICATE_DEMO["real_poi_id"]))
        if missing or mismatches or conflicting or real_duplicate is None or real_duplicate.name != DUPLICATE_DEMO["real_name"]:
            raise RuntimeError(json.dumps({"missing": missing, "name_or_category_mismatches": mismatches, "new_id_conflicts": conflicting, "real_duplicate_missing": real_duplicate is None or real_duplicate.name != DUPLICATE_DEMO["real_name"]}, ensure_ascii=False))

        affected = [found[poi_id][0] for poi_id in target_ids]
        preview = [
            {"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "category": row.category}
            for row in affected
        ]
        if not commit:
            return {"province": YUNNAN, "mode": "preview", "demo_replacements": len(DEMO_REPLACEMENTS), "demo_duplicate_deletions": 1, "category_fixes": len(COASTAL_FIXES), "affected": preview, "message": "仅预览；传入 --commit 才会备份并写入。"}

        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": YUNNAN, "reason": "resolve-demo-and-coastal-category", "rows": [_payload(row) for row in affected]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        for old_poi_id, spec in DEMO_REPLACEMENTS.items():
            found[old_poi_id][0].poi_id = spec["new_poi_id"]
        session.delete(found[DUPLICATE_DEMO["poi_id"]][0])
        for poi_id, spec in COASTAL_FIXES.items():
            row = found[poi_id][0]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        session.commit()

    settings = get_settings()
    index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
    old_ids = list(DEMO_REPLACEMENTS) + [DUPLICATE_DEMO["poi_id"]]
    vector_error = None
    vectors_deleted = 0
    try:
        index.collection.delete(ids=old_ids)
        vectors_deleted = len(old_ids)
    except Exception as exc:  # pragma: no cover - depends on local Chroma state
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": YUNNAN, "mode": "commit", "demo_replacements": len(DEMO_REPLACEMENTS), "demo_duplicate_deletions": 1, "category_fixes": len(COASTAL_FIXES), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已移除 demo 标识、删除重复 demo 行并修正滇池古渔村分类；未调用高德。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="备份并写入数据库；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(resolve(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
