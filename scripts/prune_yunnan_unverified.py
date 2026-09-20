"""删除经公开核验仍无法确认的云南目录记录。

本脚本是一个窄范围、可审计的删除动作：只接受下方明确列出的 POI，
先校验省份与名称，再完整备份当前行，最后删除 MySQL 行和本地景点向量。
默认只预览；只有显式传入 ``--commit`` 才会执行删除。脚本不调用高德，
也不会向 attractions 表增加字段。
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

# 这些记录经过多轮公开网页检索，仍只有泛化名称或地图/商业目录式结果，
# 没有足够的景点级官方/可靠资料支撑进入规划候选集。
DELETE_POIS: dict[str, str] = {
    "B037300OF0": "街心花园",
    "B036D00DO5": "抚仙湖公园",
    "B037613IHQ": "兰坪县中心广场",
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-prune-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-prune-{stamp}-{suffix}.json"
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
        mismatched = [
            {
                "poi_id": poi_id,
                "expected_name": expected_name,
                "actual_name": found[poi_id][0].name,
            }
            for poi_id, expected_name in DELETE_POIS.items()
            if poi_id in found and found[poi_id][0].name != expected_name
        ]
        if missing or mismatched:
            raise RuntimeError(
                json.dumps(
                    {"missing": missing, "name_mismatches": mismatched},
                    ensure_ascii=False,
                )
            )

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
                    "reason": "public-verification-unresolved",
                    "delete_pois": DELETE_POIS,
                    "rows": [_payload(row) for row in rows],
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
    index = ChromaAttractionIndex(
        settings.chroma_path,
        collection_name=settings.chroma_collection,
    )
    vectors_deleted = 0
    vector_error = None
    try:
        # Chroma 删除不存在的 id 是幂等的；因此即使某条记录原本没有向量，
        # 数据库删除也不会被重复运行阻塞。
        index.collection.delete(ids=list(DELETE_POIS))
        vectors_deleted = len(DELETE_POIS)
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
        "message": "已删除 MySQL 行并尝试删除本地景点向量；未调用高德。",
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
