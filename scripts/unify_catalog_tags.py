"""Normalize province, city and attraction tags to one canonical vocabulary."""

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
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.tag_vocabulary import canonicalize_tags  # noqa: E402


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"catalog-tags-before-unify-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"catalog-tags-before-unify-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def unify(*, commit: bool, backup_dir: Path) -> dict:
    init_database()
    with open_session() as session:
        provinces = list(session.scalars(select(ProvinceRow).order_by(ProvinceRow.id)))
        cities = list(session.scalars(select(CityRow).order_by(CityRow.id)))
        attractions = list(session.scalars(select(AttractionRow).order_by(AttractionRow.id)))
        changes: list[dict] = []
        for row in [*provinces, *cities, *attractions]:
            before = list(row.tags or [])
            after = canonicalize_tags(before)
            if before != after:
                changes.append(
                    {
                        "table": row.__tablename__,
                        "id": row.id,
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
                        "provinces": [_row_payload(row) for row in provinces],
                        "cities": [_row_payload(row) for row in cities],
                        "attractions": [_row_payload(row) for row in attractions],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            for row in [*provinces, *cities, *attractions]:
                row.tags = canonicalize_tags(row.tags)
            session.commit()

        vocabulary = sorted(
            {
                tag
                for row in [*provinces, *cities, *attractions]
                for tag in canonicalize_tags(row.tags)
            }
        )
        return {
            "provinces": len(provinces),
            "cities": len(cities),
            "attractions": len(attractions),
            "changed_rows": len(changes),
            "canonical_vocabulary_size": len(vocabulary),
            "canonical_vocabulary": vocabulary,
            "mapping_samples": changes[:30],
            "committed": commit,
            "backup_path": str(backup_path) if backup_path else None,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(unify(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
