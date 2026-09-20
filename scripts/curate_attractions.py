"""Curate the local attraction catalog without deleting source rows.

The default mode is a dry run.  The command now produces provenance and
quality decisions only in its JSON report; those fields are deliberately not
stored on the attraction table.  ``--commit`` is retained for compatibility
and writes an audit backup without changing the schema.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.quality import (  # noqa: E402
    description_origin,
    duplicate_winner_key,
    duration_origin,
    infer_data_source,
    public_space_kind,
    quality_flags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402


def _row_payload(row: AttractionRow, city: CityRow, province: ProvinceRow) -> dict:
    return {
        "id": row.id,
        "poi_id": row.poi_id,
        "city_id": row.city_id,
        "city": city.name,
        "province": province.name,
        "name": row.name,
        "category": row.category,
        "tags": list(row.tags or []),
        "description": row.description,
        "longitude": row.longitude,
        "latitude": row.latitude,
        "visit_duration_minutes": row.visit_duration_minutes,
    }


def _decisions(rows: list[tuple[AttractionRow, CityRow, ProvinceRow]]) -> list[dict]:
    """Return deterministic updates, one dictionary per database row."""

    groups: dict[tuple[int, str], list[AttractionRow]] = defaultdict(list)
    for row, city, _ in rows:
        normalized = normalize_attraction_name(row.name)
        if normalized:
            groups[(city.id, normalized)].append(row)

    duplicate_losers: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        winner = max(group, key=duplicate_winner_key)
        duplicate_losers.update(item.id for item in group if item.id != winner.id)

    result: list[dict] = []
    for row, city, province in rows:
        flags = quality_flags(row)
        if row.id in duplicate_losers:
            flags.append("duplicate_name")
        source = infer_data_source(row.poi_id)
        if "non_attraction_name" in flags:
            status = "excluded"
            active = False
        elif "duplicate_name" in flags:
            status = "duplicate"
            active = False
        elif public_space_kind(row.name, row.category) in {"urban_park", "urban_square"}:
            status = "secondary"
            active = True
        elif flags:
            status = "review"
            active = True
        else:
            status = "active"
            active = True
        result.append(
            {
                "row": row,
                "city": city,
                "province": province,
                "data_source": source,
                "description_origin": description_origin(row.description),
                "duration_origin": duration_origin(row.visit_duration_minutes),
                "quality_status": status,
                "quality_flags": flags,
                "is_active": active,
            }
        )
    return result


def _summary(decisions: list[dict]) -> dict:
    source_counts = Counter(item["data_source"] for item in decisions)
    status_counts = Counter(item["quality_status"] for item in decisions)
    flag_counts = Counter(
        flag for item in decisions for flag in item["quality_flags"]
    )
    inactive = [
        {
            "id": item["row"].id,
            "poi_id": item["row"].poi_id,
            "city": item["city"].name,
            "name": item["row"].name,
            "status": item["quality_status"],
            "flags": item["quality_flags"],
        }
        for item in decisions
        if not item["is_active"]
    ]
    return {
        "total": len(decisions),
        "active": sum(item["is_active"] for item in decisions),
        "inactive": len(inactive),
        "data_source": dict(sorted(source_counts.items())),
        "quality_status": dict(sorted(status_counts.items())),
        "quality_flags": dict(sorted(flag_counts.items())),
        "inactive_records": inactive,
    }


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = directory / f"attraction-curation-before-{stamp}.json"
    counter = 1
    while candidate.exists():
        candidate = directory / f"attraction-curation-before-{stamp}-{counter}.json"
        counter += 1
    return candidate


def curate(*, commit: bool, backup_dir: Path, sample_limit: int) -> dict:
    init_database()
    with open_session() as session:
        rows = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .order_by(AttractionRow.id)
            ).all()
        )
        decisions = _decisions(rows)
        summary = _summary(decisions)
        summary["mode"] = "commit" if commit else "preview"
        summary["inactive_sample"] = summary.pop("inactive_records")[:sample_limit]

        if not commit:
            return summary

        backup = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "rows": [_row_payload(row, city, province) for row, city, province in rows],
        }
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(
            json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Quality/provenance/lifecycle columns are intentionally not part of
        # the attraction schema anymore.  Keep the generated decisions in the
        # audit JSON, but do not write them back as hidden fields.
        summary["committed"] = False
        summary["message"] = "景点表已移除质量与来源字段，本命令仅生成复核报告。"
        summary["backup"] = str(backup_path)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="生成提交前审计备份；质量字段不会写入景点表",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
        help="提交前完整备份目录",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=30,
        help="输出多少条被排除记录样例",
    )
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("sample-limit 不能小于 0")
    # PowerShell may expose a legacy GBK stdout while attraction names can
    # contain punctuation such as ``•``.  Keep the JSON report lossless where
    # possible and never let reporting hide a successful database operation.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            curate(
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
