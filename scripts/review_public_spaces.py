"""Review generic park and square names in the attraction catalog.

Real scenic parks remain normal attraction candidates.  The command reports
ordinary urban parks, plazas, and transport-hub names for manual review; it
does not persist a quality or lifecycle status.
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

from backend.attractions.quality import public_space_kind  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow  # noqa: E402


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"attraction-public-spaces-before-review-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"attraction-public-spaces-before-review-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def review(*, commit: bool, backup_dir: Path, sample_limit: int) -> dict:
    init_database()
    with open_session() as session:
        rows = list(
            session.scalars(
                select(AttractionRow)
                .order_by(AttractionRow.id)
            )
        )
        decisions: list[tuple[AttractionRow, str]] = []
        for row in rows:
            kind = public_space_kind(row.name, row.category)
            if kind:
                decisions.append((row, kind))

        # The former quality/lifecycle columns were intentionally removed from
        # the attraction table.  Keep this command as a read-only audit so it
        # cannot silently reintroduce those fields.
        backup_path = None

        status_by_kind = Counter()
        for row, kind in decisions:
            status = (
                "active"
                if kind == "scenic_park"
                else "secondary"
                if kind in {"urban_park", "urban_square"}
                else "excluded"
            )
            status_by_kind[(kind, status)] += 1
        samples = [
            {
                "poi_id": row.poi_id,
                "name": row.name,
                "category": row.category,
                "kind": kind,
                "status": (
                    "active"
                    if kind == "scenic_park"
                    else "secondary"
                    if kind in {"urban_park", "urban_square"}
                    else "excluded"
                ),
            }
            for row, kind in decisions[:sample_limit]
        ]
        return {
            "selected": len(decisions),
            "kind_counts": dict(Counter(kind for _row, kind in decisions)),
            "status_counts": {
                f"{kind}:{status}": count
                for (kind, status), count in sorted(status_by_kind.items())
            },
            "committed": False,
            "message": "景点表已移除质量与启停字段，本命令仅输出复核结果。",
            "backup_path": str(backup_path) if backup_path else None,
            "preview": samples,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("sample-limit 不能小于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            review(
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
