"""Incrementally fill thin city attraction catalogs from Amap Place Search.

The command never deletes or replaces an existing attraction.  It selects
cities below ``--target-per-city``, fetches factual POIs from Amap, removes
duplicates by provider ID and normalized city-local name, and derives the
planning metadata used by this project.

Preview is the default.  Pass ``--commit`` to insert the collected rows into
MySQL.  Committed runs also write an audit JSON file under ``data/imports``.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.metadata import (  # noqa: E402
    infer_attraction_category,
    infer_interest_tags,
)
from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.models.attraction import AttractionPOI  # noqa: E402
from backend.poi.amap import AmapAttractionClient  # noqa: E402
from backend.attractions.heuristics import build_description, estimate_duration  # noqa: E402


@dataclass(frozen=True)
class CityCoverage:
    city_id: int
    city_name: str
    city_adcode: str
    province_name: str
    attraction_count: int


def _serialise(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    return value


def _select_cities(
    session,
    *,
    target_per_city: int,
    province: str | None,
    city: str | None,
    max_cities: int | None,
) -> list[CityCoverage]:
    statement = (
        select(
            CityRow.id,
            CityRow.name,
            CityRow.adcode,
            ProvinceRow.name,
            func.count(AttractionRow.id),
        )
        .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
        .outerjoin(AttractionRow, AttractionRow.city_id == CityRow.id)
        .group_by(
            CityRow.id,
            CityRow.name,
            CityRow.adcode,
            ProvinceRow.name,
            ProvinceRow.id,
        )
        .having(func.count(AttractionRow.id) < target_per_city)
        .order_by(func.count(AttractionRow.id), ProvinceRow.id, CityRow.id)
    )
    if province:
        statement = statement.where(ProvinceRow.name == province)
    if city:
        statement = statement.where(CityRow.name == city)
    if max_cities:
        statement = statement.limit(max_cities)
    return [CityCoverage(*row) for row in session.execute(statement).all()]


def _existing_identities(session) -> tuple[set[str], dict[int, set[str]]]:
    poi_ids: set[str] = set()
    names_by_city: defaultdict[int, set[str]] = defaultdict(set)
    for poi_id, city_id, name in session.execute(
        select(AttractionRow.poi_id, AttractionRow.city_id, AttractionRow.name)
    ):
        normalized_id = str(poi_id or "").strip().casefold()
        normalized_name = normalize_attraction_name(name or "")
        if normalized_id:
            poi_ids.add(normalized_id)
        if normalized_name:
            names_by_city[city_id].add(normalized_name)
    return poi_ids, dict(names_by_city)


def _prepare_candidates(
    coverage: CityCoverage,
    raw_items: list[AttractionPOI],
    *,
    target_per_city: int,
    existing_poi_ids: set[str],
    existing_names: set[str],
) -> list[AttractionRow]:
    needed = max(0, target_per_city - coverage.attraction_count)
    rows: list[AttractionRow] = []
    for item in raw_items:
        normalized_id = item.poi_id.strip().casefold()
        normalized_name = normalize_attraction_name(item.name)
        if not normalized_id or not normalized_name:
            continue
        if normalized_id in existing_poi_ids or normalized_name in existing_names:
            continue

        category = infer_attraction_category(item.name, item.category)
        tags = infer_interest_tags(item.name, category)
        duration = estimate_duration(category, tags, item.name)
        rows.append(
            AttractionRow(
                poi_id=item.poi_id,
                city_id=coverage.city_id,
                name=item.name,
                category=category,
                tags=tags,
                description=build_description(
                    city=coverage.city_name,
                    name=item.name,
                    category=category,
                    tags=tags,
                    duration=duration,
                ),
                longitude=item.longitude,
                latitude=item.latitude,
                visit_duration_minutes=duration,
            )
        )
        existing_poi_ids.add(normalized_id)
        existing_names.add(normalized_name)
        if len(rows) >= needed:
            break
    return rows


async def _fetch_cities(
    client: AmapAttractionClient,
    cities: list[CityCoverage],
    *,
    search_limit: int,
    concurrency: int,
) -> tuple[dict[int, list[AttractionPOI]], list[dict]]:
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[int, list[AttractionPOI]] = {}
    errors: list[dict] = []

    async def fetch(coverage: CityCoverage) -> None:
        async with semaphore:
            try:
                results[coverage.city_id] = await client.search_attractions(
                    coverage.city_adcode,
                    limit=search_limit,
                )
            except Exception as exc:
                errors.append(
                    {
                        "province": coverage.province_name,
                        "city": coverage.city_name,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    await asyncio.gather(*(fetch(coverage) for coverage in cities))
    return results, errors


async def run(args) -> dict:
    settings = get_settings()
    client = AmapAttractionClient(
        settings.amap_api_key,
        timeout_seconds=settings.amap_timeout_seconds,
        max_retries=settings.amap_max_retries,
    )

    with open_session() as session:
        cities = _select_cities(
            session,
            target_per_city=args.target_per_city,
            province=args.province,
            city=args.city,
            max_cities=args.max_cities,
        )
        existing_poi_ids, names_by_city = _existing_identities(session)
        fetched, errors = await _fetch_cities(
            client,
            cities,
            search_limit=args.search_limit,
            concurrency=args.concurrency,
        )

        rows: list[AttractionRow] = []
        city_results: list[dict] = []
        for coverage in cities:
            additions = _prepare_candidates(
                coverage,
                fetched.get(coverage.city_id, []),
                target_per_city=args.target_per_city,
                existing_poi_ids=existing_poi_ids,
                existing_names=names_by_city.setdefault(coverage.city_id, set()),
            )
            rows.extend(additions)
            city_results.append(
                {
                    "province": coverage.province_name,
                    "city": coverage.city_name,
                    "before": coverage.attraction_count,
                    "fetched": len(fetched.get(coverage.city_id, [])),
                    "added": len(additions),
                    "after": coverage.attraction_count + len(additions),
                }
            )

        if args.commit and rows:
            session.add_all(rows)
            session.commit()

        shortfalls = [
            item for item in city_results if item["after"] < args.target_per_city
        ]
        category_distribution = Counter(row.category for row in rows)
        payload = {
            "created_at": datetime.now().isoformat(),
            "target_per_city": args.target_per_city,
            "search_limit": args.search_limit,
            "selected_cities": len(cities),
            "successful_cities": len(cities) - len(errors),
            "failed_cities": len(errors),
            "attractions_to_add": len(rows),
            "committed": args.commit,
            "shortfall_cities": len(shortfalls),
            "category_distribution": dict(sorted(category_distribution.items())),
            "errors": errors,
            "shortfalls": shortfalls,
            "preview": [
                {
                    "poi_id": row.poi_id,
                    "city_id": row.city_id,
                    "name": row.name,
                    "category": row.category,
                    "tags": row.tags,
                    "longitude": row.longitude,
                    "latitude": row.latitude,
                }
                for row in rows[:50]
            ],
            "city_results": city_results,
        }

        if args.commit:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            audit_path = Path(args.audit_dir) / f"nationwide-attractions-{stamp}.json"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(
                json.dumps(_serialise(payload), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            payload["audit_path"] = str(audit_path)
        return payload


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-per-city", type=int, default=10)
    parser.add_argument("--search-limit", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-cities", type=int)
    parser.add_argument("--province")
    parser.add_argument("--city")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--audit-dir",
        default=str(ROOT / "data" / "imports"),
    )
    args = parser.parse_args()
    if args.target_per_city < 1:
        parser.error("--target-per-city 必须大于 0")
    if not 1 <= args.search_limit <= 50:
        parser.error("--search-limit 必须在 1 到 50 之间")
    if args.search_limit < args.target_per_city:
        parser.error("--search-limit 不能小于 --target-per-city")
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于 0")
    if args.max_cities is not None and args.max_cities < 1:
        parser.error("--max-cities 必须大于 0")
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    payload = asyncio.run(run(parse_args()))
    print(json.dumps(_serialise(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
