"""Remove a narrow set of Yunnan POIs that are not independent attractions.

The targets below were checked against the current city/name/coordinate row
and public search results.  They are either a shop/landmark inside another
scenic area, a generic project label with no attraction-level evidence, or a
duplicate spelling of a retained official scenic-area row.  The list is
intentionally explicit and audited; this command never calls Amap and never
changes the attraction schema.

Preview is the default.  ``--commit`` writes a complete backup, deletes the
MySQL rows, and removes their local Chroma documents when available.
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

DELETE_POIS: dict[str, dict[str, str]] = {
    "B0LG319HTF": {
        "name": "天涯海角(珠江源古镇西河坊南区店)",
        "reason": "名称带店且位于古镇内部，属于商业店铺，不是独立景点。",
    },
    "B0HRNZV1XD": {
        "name": "印象建水",
        "reason": "公开检索结果均指向红河建水地区的游记/目的地概念，未找到保山坐标对应的独立景点。",
    },
    "B0LG0L6QWF": {
        "name": "毛公山景区牌坊",
        "reason": "景区入口牌坊，是景区设施节点，不应作为独立规划目的地。",
    },
    "B0KAJAUX9G": {
        "name": "江景平台",
        "reason": "公开检索仅作为勐赫小镇周边地标出现，未找到独立景区资料。",
    },
    "B0M6FSXWWL": {
        "name": "昭通市桃源生态旅游景区",
        "reason": "与同坐标片区的官方命名“昭通市桃源生态旅游景区(4A)”重复，保留带等级的正式记录。",
    },
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-unverified-poi-prune-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-unverified-poi-prune-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def prune(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(
                    ProvinceRow.name == YUNNAN,
                    AttractionRow.poi_id.in_(tuple(DELETE_POIS)),
                )
                .order_by(AttractionRow.id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = [poi_id for poi_id in DELETE_POIS if poi_id not in found]
        mismatches = [
            {
                "poi_id": poi_id,
                "expected_name": spec["name"],
                "actual_name": found[poi_id][0].name,
            }
            for poi_id, spec in DELETE_POIS.items()
            if poi_id in found and found[poi_id][0].name != spec["name"]
        ]
        if missing or mismatches:
            raise RuntimeError(json.dumps({"missing": missing, "name_mismatches": mismatches}, ensure_ascii=False))

        rows = [found[poi_id][0] for poi_id in DELETE_POIS]
        candidates = [
            {
                "id": row.id,
                "poi_id": row.poi_id,
                "city": found[row.poi_id][1],
                "name": row.name,
                "category": row.category,
                "longitude": row.longitude,
                "latitude": row.latitude,
                "reason": DELETE_POIS[row.poi_id]["reason"],
            }
            for row in rows
        ]
        if not commit:
            return {
                "province": YUNNAN,
                "mode": "preview",
                "delete_count": len(rows),
                "candidates": candidates,
                "backup_path": None,
                "vectors_deleted": 0,
                "message": "仅预览；传入 --commit 才会备份并删除。",
            }

        backup_path = _backup_path(backup_dir)
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "province": YUNNAN,
                    "reason": "public-verification-and-parent-child-prune",
                    "rows": [_payload(row) for row in rows],
                    "decisions": candidates,
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

    settings = get_settings()
    index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
    vectors_deleted = 0
    vector_error = None
    try:
        index.collection.delete(ids=[row.poi_id for row in rows])
        vectors_deleted = len(rows)
    except Exception as exc:  # pragma: no cover - depends on local Chroma state
        vector_error = f"{type(exc).__name__}: {exc}"

    return {
        "province": YUNNAN,
        "mode": "commit",
        "delete_count": len(rows),
        "candidates": candidates,
        "backup_path": str(backup_path),
        "vectors_deleted": vectors_deleted,
        "vector_error": vector_error,
        "message": "已删除明确的非独立景点/重复 POI；未调用高德。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="备份并删除；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(prune(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
