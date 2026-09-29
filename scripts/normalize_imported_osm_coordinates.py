"""Normalize imported OSM attraction coordinates for AMap consumers.

AMap map and route calls expect GCJ-02 while OpenStreetMap/Nominatim supplies
WGS-84. This one-time helper reads original coordinates from import audit JSON
files and converts only matching imported rows. It never calls AMap. Preview is
the default; ``--commit`` writes only longitude/latitude in the existing table
and saves a before/after audit under ``data/imports``.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from scripts.import_public_attractions import wgs84_to_gcj02  # noqa: E402


def _load_source_coordinates(import_dir: Path) -> dict[str, list[dict[str, Any]]]:
    sources: dict[str, list[dict[str, Any]]] = {}
    for path in import_dir.glob("public-attractions-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("rows", []):
            if not isinstance(row, dict) or not str(row.get("coordinate_source_url", "")).startswith(
                "https://www.openstreetmap.org/"
            ):
                continue
            poi_id = str(row.get("poi_id") or "")
            try:
                longitude = float(row["longitude"])
                latitude = float(row["latitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if poi_id:
                sources.setdefault(poi_id, []).append(
                    {
                        "longitude": longitude,
                        "latitude": latitude,
                        "source_url": row["coordinate_source_url"],
                        "audit_file": str(path),
                    }
                )
    return sources


def _distance_meters(
    longitude: float,
    latitude: float,
    expected: tuple[float, float],
) -> float:
    earth_radius_m = 6_371_000.0
    lat1, lat2 = map(lambda value: math.radians(value), (latitude, expected[1]))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(expected[0] - longitude)
    haversine = (
        (delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * earth_radius_m * math.asin(min(1.0, math.sqrt(haversine)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--province", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument(
        "--poi-id",
        action="append",
        default=[],
        help="Limit normalization to one or more exact POI ids; may be repeated.",
    )
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--audit-dir", default=str(ROOT / "data" / "imports"))
    args = parser.parse_args()

    init_database()
    import_dir = Path(args.audit_dir)
    sources = _load_source_coordinates(import_dir)
    converted: list[dict[str, Any]] = []
    already_gcj: list[str] = []
    unmatched: list[str] = []

    with open_session() as session:
        rows = session.execute(
            select(AttractionRow, CityRow, ProvinceRow)
            .join(CityRow, AttractionRow.city_id == CityRow.id)
            .join(ProvinceRow, CityRow.province_id == ProvinceRow.id)
            .where(
                AttractionRow.poi_id.like("web-%"),
                CityRow.name == args.city,
                ProvinceRow.name == args.province,
            )
            .order_by(AttractionRow.name)
        ).all()
        for attraction, city, province in rows:
            if args.poi_id and attraction.poi_id not in args.poi_id:
                continue
            candidates = sources.get(attraction.poi_id, [])
            if not candidates:
                unmatched.append(attraction.name)
                continue
            current = (float(attraction.longitude), float(attraction.latitude))
            source = next(
                (
                    item
                    for item in candidates
                    if _distance_meters(
                        current[0],
                        current[1],
                        (item["longitude"], item["latitude"]),
                    )
                    <= 100.0
                ),
                None,
            )
            if source is None:
                if any(
                    _distance_meters(
                        current[0],
                        current[1],
                        wgs84_to_gcj02(item["longitude"], item["latitude"]),
                    )
                    <= 100.0
                    for item in candidates
                ):
                    already_gcj.append(attraction.name)
                else:
                    unmatched.append(attraction.name)
                continue

            new_longitude, new_latitude = wgs84_to_gcj02(source["longitude"], source["latitude"])
            converted.append(
                {
                    "poi_id": attraction.poi_id,
                    "name": attraction.name,
                    "province": province.name,
                    "city": city.name,
                    "old_longitude": current[0],
                    "old_latitude": current[1],
                    "new_longitude": new_longitude,
                    "new_latitude": new_latitude,
                    "source_longitude_wgs84": source["longitude"],
                    "source_latitude_wgs84": source["latitude"],
                    "source_url": source["source_url"],
                    "source_audit_file": source["audit_file"],
                }
            )
            if args.commit:
                attraction.longitude = new_longitude
                attraction.latitude = new_latitude

        if args.commit and converted:
            session.commit()

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "province": args.province,
        "city": args.city,
        "committed": bool(args.commit),
        "conversion": "WGS-84 to GCJ-02 (local mathematical transform; no AMap API call)",
        "converted_count": len(converted),
        "already_gcj_count": len(already_gcj),
        "unmatched_count": len(unmatched),
        "rows": converted,
        "already_gcj": already_gcj,
        "unmatched": unmatched,
    }
    if args.commit:
        import_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        audit_path = import_dir / f"coordinate-normalization-{stamp}.json"
        audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["audit_path"] = str(audit_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
