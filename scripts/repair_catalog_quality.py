"""Repair residual attraction quality issues using local deterministic rules.

This command recalculates categories, tags, heuristic visit windows and
source-transparent descriptions. It reports clear service/noise POIs and
high-confidence umbrella/child relationships without persisting quality or
parent/lifecycle fields. Preview is the default; ``--commit`` writes canonical
planning fields after creating a complete backup.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.descriptions import build_catalog_description  # noqa: E402
from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.metadata import infer_attraction_category, infer_interest_tags  # noqa: E402
from backend.attractions.quality import (  # noqa: E402
    description_origin,
    infer_data_source,
    public_space_kind,
    quality_flags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.tag_vocabulary import canonicalize_tags  # noqa: E402
from backend.attractions.heuristics import estimate_duration  # noqa: E402


_MANUAL_CATEGORY_OVERRIDES = {
    "洛阳桥": "历史古迹",
    "安平桥": "历史古迹",
    "赵州桥": "历史古迹",
    "卢沟桥": "历史古迹",
    "广济桥": "历史古迹",
    "五亭桥": "历史古迹",
    "猫空": "休闲度假",
    "华山文创园区中4B馆": "博物馆与展馆",
    "福州文庙": "历史古迹",
    "文庙": "历史古迹",
}
_MANUAL_NON_ATTRACTION_NAMES = {
    "v s rathod",
}
_PARENT_PAIRS = {
    # child name -> umbrella/parent name; only applied when both are in the
    # same city, so homonyms in different destinations stay independent.
    "南京夫子庙": "夫子庙秦淮风光带",
    "青海湖二郎剑景区": "青海湖国家重点风景名胜区",
    "狼渡景区": "狼渡湿地草原",
    "黑龙江勃利乌斯浑河国家森林公园": "乌斯浑河国家森林公园",
    "灵泉寺": "中国观音故里旅游区灵泉风景区",
    "少林胜迹": "泉州少林寺",
    "哈尔滨防洪纪念塔广场": "哈尔滨市人民防洪胜利纪念塔",
}
_PARENT_MARKERS = ("国家重点", "风景名胜", "旅游区", "景区", "森林公园")


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"attraction-quality-before-repair-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"attraction-quality-before-repair-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _manual_non_attraction(name: str) -> bool:
    return str(name or "").strip().casefold() in _MANUAL_NON_ATTRACTION_NAMES


def _select_parent(rows: list[AttractionRow]) -> AttractionRow:
    return max(
        rows,
        key=lambda row: (
            row.poi_id.startswith("mct-5a-"),
            any(marker in row.name for marker in _PARENT_MARKERS),
            len(normalize_attraction_name(row.name)),
            -row.id,
        ),
    )


def _infer_parent_relations(rows: list[AttractionRow]) -> dict[int, str]:
    by_city: dict[int, dict[str, AttractionRow]] = defaultdict(dict)
    for row in rows:
        by_city[row.city_id][normalize_attraction_name(row.name)] = row

    relations: dict[int, str] = {}
    for city_rows in by_city.values():
        for child_name, parent_name in _PARENT_PAIRS.items():
            child = city_rows.get(normalize_attraction_name(child_name))
            parent = city_rows.get(normalize_attraction_name(parent_name))
            if child and parent and child.id != parent.id:
                relations[child.id] = parent.poi_id

    # Same-coordinate groups are strong evidence for curated umbrella/child
    # records after approximate coordinates have been corrected.
    groups: dict[tuple[int, float, float], list[AttractionRow]] = defaultdict(list)
    for row in rows:
        if row.longitude is None or row.latitude is None:
            continue
        groups[(row.city_id, round(float(row.longitude), 5), round(float(row.latitude), 5))].append(row)
    for group in groups.values():
        if len(group) < 2 or not any(infer_data_source(row.poi_id) == "curated" for row in group):
            continue
        parent = _select_parent(group)
        for row in group:
            if row.id != parent.id and row.id not in relations:
                relations[row.id] = parent.poi_id
    return relations


def repair(*, commit: bool, backup_dir: Path) -> dict:
    init_database()
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .order_by(AttractionRow.id)
            ).all()
        )
        rows = [row for row, _city in pairs]
        parent_relations = _infer_parent_relations(rows)
        changes: list[dict] = []
        category_changes = 0
        duration_changes = 0
        description_changes = 0
        excluded = 0
        secondary = 0

        for row, city in pairs:
            if infer_data_source(row.poi_id) == "demo":
                continue

            category = _MANUAL_CATEGORY_OVERRIDES.get(row.name) or infer_attraction_category(
                row.name, row.category
            )
            tags = canonicalize_tags(infer_interest_tags(row.name, category))
            duration = estimate_duration(category, tags, row.name)
            description = row.description
            if description_origin(row.description) in {"template", "derived"}:
                description = build_catalog_description(
                    city=city.name,
                    name=row.name,
                    category=category,
                    tags=tags,
                    duration_minutes=duration,
                )
            before = {
                "category": row.category,
                "tags": list(row.tags or []),
                "duration": row.visit_duration_minutes,
                "description": row.description,
                "parent_relation": parent_relations.get(row.id),
            }

            row.category = category
            row.tags = tags
            row.visit_duration_minutes = duration
            if description != row.description:
                row.description = description
                description_changes += 1
            if before["category"] != category:
                category_changes += 1
            if before["duration"] != duration:
                duration_changes += 1

            flags = quality_flags(row)
            if _manual_non_attraction(row.name):
                if "non_attraction_name" not in flags:
                    flags.append("non_attraction_name")
            parent_id = parent_relations.get(row.id)
            if parent_id:
                flags.append("parent_child_overlap")
            flags = sorted(set(flags))

            if "non_attraction_name" in flags:
                selection_status = "excluded"
                excluded += 1
            elif parent_id or public_space_kind(row.name, row.category) in {"urban_park", "urban_square"}:
                selection_status = "secondary"
                secondary += 1
            elif flags:
                selection_status = "review"
            else:
                selection_status = "active"

            after = {
                "category": row.category,
                "tags": list(row.tags or []),
                "duration": row.visit_duration_minutes,
                "description": row.description,
                "parent_relation": parent_id,
                "selection_status": selection_status,
                "quality_flags": flags,
            }
            if before != after:
                changes.append(
                    {
                        "poi_id": row.poi_id,
                        "city": city.name,
                        "name": row.name,
                        "before": before,
                        "after": after,
                    }
                )

        backup_path = None
        if commit and changes:
            backup_path = _backup_path(backup_dir)
            backup_path.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "rows": [_payload(row) for row in rows],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            session.commit()
        return {
            "rows": len(rows),
            "changed_rows": len(changes),
            "category_changes": category_changes,
            "duration_changes": duration_changes,
            "description_changes": description_changes,
            "parent_child_relations": len(parent_relations),
            "excluded_rows": excluded,
            "secondary_rows": secondary,
            "committed": commit,
            "backup_path": str(backup_path) if backup_path else None,
            "preview": changes[:30],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(repair(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
