"""Fill empty attraction coordinates from the stored Amap POI IDs.

Only rows with a missing longitude or latitude are selected. Existing valid
coordinates are preserved, and no attraction identity or other planning field
is changed. Preview is the default; ``--commit`` writes the verified values.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.amap_mcp import AmapMcpClient  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.poi.amap import AmapAttractionClient  # noqa: E402
from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402


def _location(value) -> tuple[float, float] | None:
    if not isinstance(value, str) or "," not in value:
        return None
    try:
        longitude, latitude = (float(part.strip()) for part in value.split(",", 1))
    except (TypeError, ValueError):
        return None
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        return None
    return longitude, latitude


async def _fetch_coordinates(
    rows,
    *,
    concurrency: int,
    retries: int,
) -> tuple[dict[str, tuple[float, float]], list[dict]]:
    settings = get_settings()
    client = AmapMcpClient(
        settings.amap_api_key,
        endpoint=settings.amap_mcp_url,
        timeout_seconds=settings.amap_mcp_timeout_seconds,
        sse_read_timeout_seconds=settings.amap_mcp_sse_read_timeout_seconds,
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    coordinates: dict[str, tuple[float, float]] = {}
    failures: list[dict] = []

    async with client.session_scope():
        async def fetch(row) -> None:
            async with semaphore:
                last_error: Exception | None = None
                for attempt in range(retries + 1):
                    try:
                        payload = await client.call_tool(
                            "maps_search_detail",
                            {"id": row.poi_id},
                            operation="coordinate_backfill",
                        )
                        location = _location(payload.get("location"))
                        if location is None:
                            raise ValueError("详情缺少有效 location")
                        coordinates[row.poi_id] = location
                        return
                    except Exception as exc:  # noqa: BLE001 - retry transient details
                        last_error = exc
                        if attempt < retries:
                            await asyncio.sleep(0.4 * (attempt + 1))
                try:
                    raise last_error or RuntimeError("详情查询失败")
                except Exception as exc:  # noqa: BLE001 - continue other POIs
                    failures.append(
                        {
                            "poi_id": row.poi_id,
                            "name": row.name,
                            "city": row.city.name,
                            "error": type(exc).__name__,
                            "message": str(exc),
                        }
                    )

        await asyncio.gather(*(fetch(row) for row in rows))
    # A small number of historical IDs are no longer resolvable by the MCP
    # detail tool. Fall back to one REST place search per affected city and
    # accept only an exact (normalized) name match.
    unresolved = [row for row in rows if row.poi_id not in coordinates]
    if unresolved:
        # Replace the provisional direct-detail errors with the final outcome
        # after the REST fallback, so each POI appears at most once in the
        # audit report.
        failures = []
        rest_client = AmapAttractionClient(
            settings.amap_api_key,
            timeout_seconds=settings.amap_timeout_seconds,
            max_retries=settings.amap_max_retries,
        )
        by_city: dict[str, list] = {}
        for row in unresolved:
            by_city.setdefault(row.city.adcode, []).append(row)

        async def fallback_city(adcode: str, city_rows: list) -> None:
            try:
                found = await rest_client.search_attractions(adcode, limit=50)
            except Exception as exc:  # noqa: BLE001 - retain direct failures
                for row in city_rows:
                    failures.append(
                        {
                            "poi_id": row.poi_id,
                            "name": row.name,
                            "city": row.city.name,
                            "error": type(exc).__name__,
                            "message": f"详情与 REST 检索均失败：{exc}",
                        }
                    )
                return

            by_name = {
                normalize_attraction_name(item.name): item
                for item in found
                if item.longitude is not None and item.latitude is not None
            }
            for row in city_rows:
                item = by_name.get(normalize_attraction_name(row.name))
                if item is None:
                    failures.append(
                        {
                            "poi_id": row.poi_id,
                            "name": row.name,
                            "city": row.city.name,
                            "error": "NameNotFound",
                            "message": "详情与城市检索均未找到同名 POI",
                        }
                    )
                else:
                    coordinates[row.poi_id] = (item.longitude, item.latitude)

        # Keep the fallback city searches sequential: the REST endpoint has a
        # lower per-key QPS limit than the MCP detail calls.
        for adcode, city_rows in by_city.items():
            await fallback_city(adcode, city_rows)
    return coordinates, failures


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--audit-dir", default=str(ROOT / "data" / "imports"))
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于 0")
    if args.retries < 0:
        parser.error("--retries 不能小于 0")
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    with open_session() as session:
        rows = list(
            session.scalars(
                select(AttractionRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(
                    (AttractionRow.longitude.is_(None))
                    | (AttractionRow.latitude.is_(None))
                )
                .order_by(AttractionRow.id)
            )
        )
        # Relationships are already mapped; make the city object available to
        # the async reporting closure before the session remains open.
        for row in rows:
            _ = row.city.name
        coordinates, failures = asyncio.run(
            _fetch_coordinates(
                rows,
                concurrency=args.concurrency,
                retries=args.retries,
            )
        )

        updated = []
        for row in rows:
            location = coordinates.get(row.poi_id)
            if location is None:
                continue
            longitude, latitude = location
            if row.longitude is None:
                row.longitude = longitude
            if row.latitude is None:
                row.latitude = latitude
            updated.append(
                {
                    "poi_id": row.poi_id,
                    "name": row.name,
                    "city": row.city.name,
                    "longitude": row.longitude,
                    "latitude": row.latitude,
                }
            )

        if args.commit and updated:
            session.commit()

        payload = {
            "created_at": datetime.now().isoformat(),
            "selected": len(rows),
            "resolved": len(updated),
            "failed": len(failures),
            "committed": args.commit,
            "failures": failures,
            "preview": updated[:30],
        }
        if args.commit:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            audit_path = Path(args.audit_dir) / f"attraction-coordinates-{stamp}.json"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            payload["audit_path"] = str(audit_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
