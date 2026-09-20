"""Replace generic template descriptions with source-transparent summaries.

Only descriptions containing the old generated markers are changed.  The new
text uses the existing city, name, category, tags and estimated duration; it
does not invent ratings, opening hours, prices, or historical facts.
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

from backend.attractions.descriptions import build_catalog_description  # noqa: E402
from backend.attractions.quality import quality_flags  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"attraction-descriptions-before-refresh-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"attraction-descriptions-before-refresh-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def refresh(*, commit: bool, backup_dir: Path, sample_limit: int) -> dict:
    init_database()
    with open_session() as session:
        rows = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .where(
                    AttractionRow.is_active.is_(True),
                    AttractionRow.description_origin == "template",
                )
                .order_by(AttractionRow.id)
            ).all()
        )
        updates: list[dict] = []
        for row, city in rows:
            description = build_catalog_description(
                city=city.name,
                name=row.name,
                category=row.category,
                tags=row.tags,
                duration_minutes=row.visit_duration_minutes,
            )
            updates.append(
                {
                    "row": row,
                    "city": city.name,
                    "poi_id": row.poi_id,
                    "name": row.name,
                    "before": row.description,
                    "after": description,
                }
            )

        backup_path = None
        if commit:
            backup_path = _backup_path(backup_dir)
            backup_path.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "rows": [
                            _row_payload(row)
                            for row in session.scalars(
                                select(AttractionRow).order_by(AttractionRow.id)
                            )
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            for item in updates:
                row = item["row"]
                row.description = item["after"]
                row.description_origin = "derived"
                row.quality_flags = quality_flags(row)
                if "non_attraction_name" in row.quality_flags:
                    row.quality_status = "excluded"
                    row.is_active = False
                elif row.quality_flags:
                    row.quality_status = "review"
                    row.is_active = True
                else:
                    row.quality_status = "active"
                    row.is_active = True
            session.commit()

        return {
            "selected_template_descriptions": len(rows),
            "updated": len(updates) if commit else 0,
            "committed": commit,
            "backup_path": str(backup_path) if backup_path else None,
            "remaining_template_descriptions": 0 if commit else len(rows),
            "preview": [
                {
                    "city": item["city"],
                    "poi_id": item["poi_id"],
                    "name": item["name"],
                    "before": item["before"],
                    "after": item["after"],
                }
                for item in updates[:sample_limit]
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
    )
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("sample-limit 不能小于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            refresh(
                commit=args.commit,
                backup_dir=args.backup_dir,
                sample_limit=args.sample_limit,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
