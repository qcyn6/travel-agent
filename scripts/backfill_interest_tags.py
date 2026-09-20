"""Backfill explainable attraction tags from local fields only.

This maintenance command derives tags from the attraction name and category
already stored in MySQL.  It does not claim external facts and never replaces
non-empty tags supplied by a maintainer.  Preview is the default; pass
``--commit`` to write the derived tags.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import init_database, open_session  # noqa: E402
from backend.attractions.metadata import infer_interest_tags  # noqa: E402
from backend.database.tables import AttractionRow  # noqa: E402


def _has_tags(value) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    args = parser.parse_args()

    init_database()
    with open_session() as session:
        statement = select(AttractionRow).order_by(AttractionRow.id.asc())
        rows = list(session.scalars(statement))

        updates: list[tuple[AttractionRow, list[str]]] = []
        skipped_existing = 0
        for row in rows:
            if _has_tags(row.tags):
                skipped_existing += 1
                continue
            updates.append((row, infer_interest_tags(row.name, row.category)))

        distribution = Counter(tag for _, tags in updates for tag in tags)
        preview = [
            {"poi_id": row.poi_id, "name": row.name, "tags": tags}
            for row, tags in updates[:20]
        ]

        if args.commit:
            for row, tags in updates:
                row.tags = tags
            session.commit()

        result = {
            "scope": "all",
            "selected": len(rows),
            "updated": len(updates),
            "skipped_existing": skipped_existing,
            "committed": args.commit,
            "tag_distribution": dict(sorted(distribution.items())),
            "preview": preview,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
