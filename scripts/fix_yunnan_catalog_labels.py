"""Apply a small, evidence-backed Yunnan category correction and one deletion.

Only existing planning fields are changed.  The deletion is limited to a
residential real-estate POI that was labelled as a scenic area but has no
independent visitor attraction evidence.  Preview is the default; ``--commit``
backs up affected rows, updates MySQL, and removes the deleted vector.
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

UPDATES: dict[str, dict[str, Any]] = {
    "B0FFK432QQ": {
        "name": "圣托里尼大理",
        "category": "休闲度假",
        "tags": ["自然", "建筑", "休闲", "摄影"],
        "duration": 120,
        "description": "大理圣托里尼（理想邦）位于洱海东岸海东镇，是沿山势展开的建筑与度假片区。白色建筑、海崖视野和旅拍体验是主要看点，适合建筑摄影、滨水观景和轻度假；开放、预约、交通和收费请以实时公开信息为准。",
    },
    "B0FFH0AUQJ": {
        "name": "金麟湾爱情小镇",
        "category": "休闲度假",
        "tags": ["休闲", "花卉", "摄影", "亲子"],
        "duration": 180,
        "description": "金麟湾爱情小镇是曲靖麒麟区以花海、休闲观光和主题体验为主的文旅小镇，片区还包含花海、商业和休闲项目。适合花卉观赏、摄影、亲子和轻度假；开放、预约、交通和收费请以实时公开信息为准。",
    },
    "B0FFKC5A25": {
        "name": "梨花谷",
        "category": "生态动植物",
        "tags": ["自然", "花卉", "休闲", "摄影"],
        "duration": 120,
        "description": "昆明呈贡吴家营梨花谷以梨林花季、近郊休闲和摄影为主要体验，适合花卉观赏、短途散步和春季踏青；花期和现场活动具有季节性，开放安排请以实时公开信息为准。",
    },
}

DELETE: dict[str, dict[str, str]] = {
    "B0GK6OU6S8": {
        "name": "融创·凤凰山国际文旅小镇",
        "reason": "公开资料主要显示为昭通凤凰山住宅/社区项目，未找到独立景区运营资料。",
    }
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-label-fix-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-label-fix-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def fix(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    targets = tuple(UPDATES) + tuple(DELETE)
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == YUNNAN, AttractionRow.poi_id.in_(targets))
                .order_by(AttractionRow.id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = [poi_id for poi_id in targets if poi_id not in found]
        mismatches = [
            {"poi_id": poi_id, "expected_name": spec["name"], "actual_name": found[poi_id][0].name}
            for poi_id, spec in {**UPDATES, **DELETE}.items()
            if poi_id in found and found[poi_id][0].name != spec["name"]
        ]
        if missing or mismatches:
            raise RuntimeError(json.dumps({"missing": missing, "name_mismatches": mismatches}, ensure_ascii=False))

        updates = [found[poi_id][0] for poi_id in UPDATES]
        deletions = [found[poi_id][0] for poi_id in DELETE]
        preview = {
            "updates": [
                {"poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "before_category": row.category, "after_category": UPDATES[row.poi_id]["category"]}
                for row in updates
            ],
            "deletions": [
                {"poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "category": row.category, "reason": DELETE[row.poi_id]["reason"]}
                for row in deletions
            ],
        }
        if not commit:
            return {"province": YUNNAN, "mode": "preview", **preview, "backup_path": None, "vectors_deleted": 0}

        backup_path = _backup_path(backup_dir)
        backup_path.write_text(
            json.dumps(
                {"created_at": datetime.now().isoformat(timespec="seconds"), "province": YUNNAN, "rows": [_payload(row) for row in [*updates, *deletions]], "decisions": preview},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        for poi_id, spec in UPDATES.items():
            row = found[poi_id][0]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.visit_duration_minutes = spec["duration"]
            row.description = spec["description"]
        for row in deletions:
            session.delete(row)
        session.commit()

    settings = get_settings()
    index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
    vector_error = None
    vectors_deleted = 0
    try:
        index.collection.delete(ids=[row.poi_id for row in deletions])
        vectors_deleted = len(deletions)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": YUNNAN, "mode": "commit", **preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(fix(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
