"""Replace synthetic ``demo-*`` attractions with real Amap POIs.

The command is a maintenance import, not part of candidate retrieval.  It
queries Amap once per city containing demo rows, keeps only provider POIs that
are not already in the catalog, and updates the synthetic rows in place.  If a
city returns fewer candidates, unresolved demo rows are reported and retained;
the attraction table no longer has a lifecycle field with which to hide them.

Preview is the default.  Pass ``--commit`` to write the replacement and a
complete backup under ``data/backups``.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.descriptions import build_catalog_description  # noqa: E402
from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.metadata import (  # noqa: E402
    infer_attraction_category,
    infer_interest_tags,
)
from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.models.attraction import AttractionPOI  # noqa: E402
from backend.poi.amap import AmapAttractionClient  # noqa: E402
from backend.attractions.heuristics import estimate_duration  # noqa: E402


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"attraction-demo-before-replacement-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"attraction-demo-before-replacement-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row: AttractionRow) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def _normalised_candidates(items: list[AttractionPOI]) -> list[AttractionPOI]:
    result: list[AttractionPOI] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in items:
        poi_id = item.poi_id.strip().casefold()
        name = normalize_attraction_name(item.name)
        if not poi_id or not name or poi_id in seen_ids or name in seen_names:
            continue
        seen_ids.add(poi_id)
        seen_names.add(name)
        result.append(item)
    return result


async def _fetch(
    client: AmapAttractionClient,
    cities: list[CityRow],
    *,
    limit: int,
    concurrency: int,
) -> tuple[dict[int, list[AttractionPOI]], list[dict]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    fetched: dict[int, list[AttractionPOI]] = {}
    errors: list[dict] = []

    async def fetch_city(city: CityRow) -> None:
        async with semaphore:
            try:
                fetched[city.id] = await client.search_attractions(
                    city.adcode,
                    limit=limit,
                )
            except Exception as exc:  # noqa: BLE001 - report per-city provider failures
                errors.append(
                    {
                        "city_id": city.id,
                        "city": city.name,
                        "adcode": city.adcode,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    await asyncio.gather(*(fetch_city(city) for city in cities))
    return fetched, errors


def _prepare_updates(
    demo_by_city: dict[int, list[AttractionRow]],
    cities_by_id: dict[int, CityRow],
    fetched: dict[int, list[AttractionPOI]],
    existing_ids: set[str],
    existing_names: set[tuple[int, str]],
) -> tuple[list[dict], list[dict], list[AttractionRow], list[dict]]:
    updates: list[dict] = []
    insertions: list[dict] = []
    retained_demo_rows: list[AttractionRow] = []
    unresolved: list[dict] = []
    reserved_ids = set(existing_ids)
    reserved_names = set(existing_names)

    for city_id, demo_rows in demo_by_city.items():
        city = cities_by_id[city_id]
        candidates = _normalised_candidates(fetched.get(city_id, []))
        candidates = [
            item
            for item in candidates
            if item.poi_id.strip().casefold() not in reserved_ids
        ]
        used_candidate_ids: set[str] = set()
        used_candidate_names: set[str] = set()

        unresolved_rows: list[AttractionRow] = []
        pending_records: list[dict] = []
        for row in sorted(demo_rows, key=lambda item: item.id):
            old_name = row.name
            old_poi_id = row.poi_id
            old_normalized_name = normalize_attraction_name(old_name)
            candidate = next(
                (
                    item
                    for item in candidates
                    if normalize_attraction_name(item.name) == old_normalized_name
                    and item.poi_id.strip().casefold() not in used_candidate_ids
                    and (city_id, normalize_attraction_name(item.name))
                    not in reserved_names
                ),
                None,
            )
            if candidate is None:
                unresolved_rows.append(row)
                pending_records.append(
                    {
                        "row": row,
                        "city": city.name,
                        "poi_id": old_poi_id,
                        "name": old_name,
                    }
                )
                continue

            poi_id = candidate.poi_id.strip()
            name = candidate.name.strip()
            category = infer_attraction_category(name, candidate.category)
            tags = infer_interest_tags(name, category)
            duration = estimate_duration(category, tags, name)
            description = build_catalog_description(
                city=city.name,
                name=name,
                category=category,
                tags=tags,
                duration_minutes=duration,
            )
            updates.append(
                {
                    "row": row,
                    "city": city,
                    "old_poi_id": old_poi_id,
                    "old_name": old_name,
                    "new_poi_id": poi_id,
                    "new_name": name,
                    "category": category,
                    "tags": tags,
                    "description": description,
                    "longitude": candidate.longitude,
                    "latitude": candidate.latitude,
                    "visit_duration_minutes": duration,
                }
            )
            used_candidate_ids.add(poi_id.casefold())
            used_candidate_names.add(normalize_attraction_name(name))
            reserved_ids.add(poi_id.casefold())
            reserved_names.add((city_id, normalize_attraction_name(name)))

        # A non-exact provider result is inserted as its own POI rather than
        # being assigned a synthetic attraction's identity.  This avoids
        # turning (for example) a museum row into an unrelated city park.
        retained_demo_rows.extend(unresolved_rows)
        for candidate in candidates:
            if not unresolved_rows:
                break
            poi_id = candidate.poi_id.strip()
            name = candidate.name.strip()
            normalized_name = normalize_attraction_name(name)
            if (
                poi_id.casefold() in used_candidate_ids
                or normalized_name in used_candidate_names
                or (city_id, normalized_name) in reserved_names
            ):
                continue
            category = infer_attraction_category(name, candidate.category)
            tags = infer_interest_tags(name, category)
            duration = estimate_duration(category, tags, name)
            insertions.append(
                {
                    "row": None,
                    "city": city,
                    "old_poi_id": None,
                    "old_name": None,
                    "new_poi_id": poi_id,
                    "new_name": name,
                    "category": category,
                    "tags": tags,
                    "description": build_catalog_description(
                        city=city.name,
                        name=name,
                        category=category,
                        tags=tags,
                        duration_minutes=duration,
                    ),
                    "longitude": candidate.longitude,
                    "latitude": candidate.latitude,
                    "visit_duration_minutes": duration,
                }
            )
            used_candidate_ids.add(poi_id.casefold())
            used_candidate_names.add(normalized_name)
            reserved_ids.add(poi_id.casefold())
            reserved_names.add((city_id, normalized_name))
            unresolved_rows.pop(0)

        remaining_ids = {row.id for row in unresolved_rows}
        unresolved.extend(
            record
            for record in pending_records
            if record["row"].id in remaining_ids
        )

    return updates, insertions, retained_demo_rows, unresolved


def _apply_update(item: dict) -> None:
    row: AttractionRow = item["row"]
    row.poi_id = item["new_poi_id"]
    row.name = item["new_name"]
    row.category = item["category"]
    row.tags = item["tags"]
    row.description = item["description"]
    row.longitude = item["longitude"]
    row.latitude = item["latitude"]
    row.visit_duration_minutes = item["visit_duration_minutes"]


def _new_row(item: dict) -> AttractionRow:
    row = AttractionRow(city_id=item["city"].id, name=item["new_name"])
    row.poi_id = item["new_poi_id"]
    row.category = item["category"]
    row.tags = item["tags"]
    row.description = item["description"]
    row.longitude = item["longitude"]
    row.latitude = item["latitude"]
    row.visit_duration_minutes = item["visit_duration_minutes"]
    return row


async def replace_demo(
    *,
    commit: bool,
    backup_dir: Path,
    limit_per_city: int,
    concurrency: int,
    retire_only: bool = False,
) -> dict:
    init_database()
    settings = get_settings()
    with open_session() as session:
        joined = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .where(AttractionRow.poi_id.like("demo-%"))
                .order_by(AttractionRow.city_id, AttractionRow.id)
            ).all()
        )
        if not joined:
            return {"demo_rows": 0, "replacements": 0, "unresolved": 0}

        if retire_only:
            return {
                "demo_rows": len(joined),
                "retained_demo_rows": len(joined),
                "committed": False,
                "backup_path": None,
                "message": "景点表已移除生命周期字段，不能仅停用 demo 行；请改用替换或人工清理。",
            }

        demo_by_city: dict[int, list[AttractionRow]] = defaultdict(list)
        cities_by_id: dict[int, CityRow] = {}
        for row, city in joined:
            demo_by_city[city.id].append(row)
            cities_by_id[city.id] = city

        existing_ids: set[str] = set()
        existing_names: set[tuple[int, str]] = set()
        for row in session.scalars(
            select(AttractionRow).where(~AttractionRow.poi_id.like("demo-%"))
        ):
            existing_ids.add(row.poi_id.strip().casefold())
            normalized = normalize_attraction_name(row.name)
            if normalized:
                existing_names.add((row.city_id, normalized))

        client = AmapAttractionClient(
            settings.amap_api_key,
            timeout_seconds=settings.amap_timeout_seconds,
            max_retries=settings.amap_max_retries,
        )
        fetched, errors = await _fetch(
            client,
            list(cities_by_id.values()),
            limit=limit_per_city,
            concurrency=concurrency,
        )
        updates, insertions, retained_demo_rows, unresolved = _prepare_updates(
            demo_by_city,
            cities_by_id,
            fetched,
            existing_ids,
            existing_names,
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
                _apply_update(item)
            for item in insertions:
                session.add(_new_row(item))
            session.commit()

        return {
            "demo_rows": len(joined),
            "cities": len(cities_by_id),
            "provider_results": sum(len(items) for items in fetched.values()),
            "exact_replacements": len(updates),
            "real_poi_insertions": len(insertions),
            "retained_demo_rows": len(retained_demo_rows),
            "unresolved": len(unresolved),
            "provider_errors": errors,
            "unresolved_records": [
                {key: value for key, value in item.items() if key != "row"}
                for item in unresolved
            ],
            "source_after_replacement": "amap",
            "committed": commit,
            "backup_path": str(backup_path) if backup_path else None,
            "preview": [
                {
                    "city": item["city"].name,
                    "action": "replace" if item["row"] is not None else "insert",
                    "old_name": item["old_name"],
                    "new_name": item["new_name"],
                    "new_poi_id": item["new_poi_id"],
                }
                for item in (updates + insertions)[:30]
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="写入真实 POI；默认仅预览")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
        help="提交前完整备份目录",
    )
    parser.add_argument("--limit-per-city", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--retire-only",
        action="store_true",
        help="不再调用高德，仅停用当前仍 active 的 demo 行",
    )
    args = parser.parse_args()
    if not 1 <= args.limit_per_city <= 50:
        parser.error("limit-per-city 必须在 1 到 50 之间")
    if args.concurrency < 1:
        parser.error("concurrency 必须大于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            asyncio.run(
                replace_demo(
                    commit=args.commit,
                    backup_dir=args.backup_dir,
                    limit_per_city=args.limit_per_city,
                    concurrency=args.concurrency,
                    retire_only=args.retire_only,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
