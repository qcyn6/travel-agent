"""Refresh the locally generated attraction catalog.

The demo keeps the existing POI identity, city relationship and coordinates so
the planning and route flows remain connected. Categories, tags, descriptions
and planning facts are generated locally from the stored name/category fields;
no web page or attraction API is called. A backup of the old attraction rows
and facts is written before a committed replacement.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.attractions.heuristics import build_description, estimate_duration  # noqa: E402, F401
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.attractions.metadata import (  # noqa: E402
    infer_attraction_category as classify_category,
)
from backend.attractions.metadata import infer_interest_tags  # noqa: E402


def serialise(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: serialise(item) for key, item in value.items()}
    return value


def row_dict(row) -> dict:
    return {column.name: serialise(getattr(row, column.name)) for column in row.__table__.columns}


def write_backup(session, *, out_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"attraction-data-before-ai-demo-{stamp}.json"
    payload = {
        "created_at": datetime.now().isoformat(),
        "tables": {
            "attractions": [row_dict(row) for row in session.scalars(select(AttractionRow))],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="写入演示数据；默认只预览")
    parser.add_argument(
        "--backup-dir",
        default=str(ROOT / "data" / "backups"),
        help="旧景点数据备份目录",
    )
    args = parser.parse_args()

    init_database()
    with open_session() as session:
        rows = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .order_by(AttractionRow.id)
            ).all()
        )
        preview = []
        tag_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        for attraction, city in rows:
            tags = infer_interest_tags(attraction.name, attraction.category)
            category = classify_category(attraction.name, attraction.category)
            duration = estimate_duration(category, tags, attraction.name)
            tag_counts.update(tags)
            category_counts[category] += 1
            if len(preview) < 20:
                preview.append(
                    {
                        "poi_id": attraction.poi_id,
                        "city": city.name,
                        "name": attraction.name,
                        "category": category,
                        "tags": tags,
                        "duration": duration,
                    }
                )

        result = {
            "attractions_selected": len(rows),
            "descriptions_to_update": len(rows),
            "duration_updates": len(rows),
            "committed": args.commit,
            "category_distribution": dict(sorted(category_counts.items())),
            "tag_distribution": dict(sorted(tag_counts.items())),
            "preview": preview,
        }

        if args.commit:
            backup_path = write_backup(session, out_dir=Path(args.backup_dir))
            for attraction, city in rows:
                tags = infer_interest_tags(attraction.name, attraction.category)
                category = classify_category(attraction.name, attraction.category)
                duration = estimate_duration(category, tags, attraction.name)
                description = build_description(
                    city=city.name,
                    name=attraction.name,
                    category=category,
                    tags=tags,
                    duration=duration,
                )
                attraction.category = category
                attraction.tags = tags
                attraction.description = description
                attraction.visit_duration_minutes = duration
            session.commit()
            result["backup_path"] = str(backup_path)
            result["durations_updated"] = len(rows)

        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
