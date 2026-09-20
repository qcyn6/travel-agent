"""Improve local attraction categories, tags and missing coordinates.

This is a deterministic maintenance command for the demo catalog.  It keeps
POI identity, city relationships, descriptions and existing coordinates.  For
missing coordinates it uses known landmark anchors, approximate city centers
and a stable small offset so route and map demonstrations have usable points.
The values are intentionally local demo metadata, not a claim of official
geocoding accuracy.
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

from backend.attractions.metadata import (  # noqa: E402
    city_coordinate_anchor,
    coordinate_for_attraction,
    infer_attraction_category,
    infer_interest_tags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import (  # noqa: E402
    AttractionRow,
    CityRow,
    ProvinceRow,
)


def _valid_coordinate(value: float | None, *, longitude: bool) -> bool:
    if value is None:
        return False
    value = float(value)
    if longitude:
        return -180.0 <= value <= 180.0
    return -90.0 <= value <= 90.0


def _serialise(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    return value


def _backup(session, *, out_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"attraction-metadata-before-enrichment-{stamp}.json"
    rows = session.scalars(select(AttractionRow).order_by(AttractionRow.id)).all()
    payload = {
        "created_at": datetime.now().isoformat(),
        "tables": {
            "attractions": [
                {
                    column.name: _serialise(getattr(row, column.name))
                    for column in row.__table__.columns
                }
                for row in rows
            ]
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    parser.add_argument(
        "--backup-dir",
        default=str(ROOT / "data" / "backups"),
        help="变更前景点数据备份目录",
    )
    args = parser.parse_args()

    init_database()
    with open_session() as session:
        joined_rows = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .order_by(AttractionRow.id)
            ).all()
        )
        backup_path = (
            _backup(session, out_dir=Path(args.backup_dir)) if args.commit else None
        )

        observed_by_city: defaultdict[int, list[tuple[float, float]]] = defaultdict(list)
        for attraction, city, _province in joined_rows:
            if _valid_coordinate(attraction.longitude, longitude=True) and _valid_coordinate(
                attraction.latitude, longitude=False
            ):
                observed_by_city[city.id].append(
                    (float(attraction.longitude), float(attraction.latitude))
                )

        anchors = {
            city.id: city_coordinate_anchor(
                city_name=city.name,
                province_name=province.name,
                observed_coordinates=observed_by_city[city.id],
            )
            for _attraction, city, province in joined_rows
        }

        category_changes = 0
        tag_changes = 0
        coordinate_fills = 0
        preserved_coordinates = 0
        category_distribution: Counter[str] = Counter()
        tag_distribution: Counter[str] = Counter()
        preview: list[dict] = []

        for attraction, city, province in joined_rows:
            category = infer_attraction_category(attraction.name, attraction.category)
            tags = infer_interest_tags(attraction.name, category)
            category_distribution[category] += 1
            tag_distribution.update(tags)

            category_changed = attraction.category != category
            tags_changed = list(attraction.tags or []) != tags
            if category_changed:
                category_changes += 1
            if tags_changed:
                tag_changes += 1

            has_coordinates = _valid_coordinate(
                attraction.longitude, longitude=True
            ) and _valid_coordinate(attraction.latitude, longitude=False)
            if has_coordinates:
                longitude = float(attraction.longitude)
                latitude = float(attraction.latitude)
                preserved_coordinates += 1
            else:
                longitude, latitude = coordinate_for_attraction(
                    poi_id=attraction.poi_id,
                    name=attraction.name,
                    city_name=city.name,
                    province_name=province.name,
                    anchor=anchors[city.id],
                )
                coordinate_fills += 1

            if len(preview) < 20:
                preview.append(
                    {
                        "poi_id": attraction.poi_id,
                        "city": city.name,
                        "name": attraction.name,
                        "category": category,
                        "tags": tags,
                        "longitude": longitude,
                        "latitude": latitude,
                    }
                )

            if args.commit:
                attraction.category = category
                attraction.tags = tags
                attraction.longitude = longitude
                attraction.latitude = latitude

        result = {
            "selected": len(joined_rows),
            "category_changes": category_changes,
            "tag_changes": tag_changes,
            "coordinates_filled": coordinate_fills,
            "coordinates_preserved": preserved_coordinates,
            "committed": args.commit,
            "category_distribution": dict(sorted(category_distribution.items())),
            "tag_distribution": dict(sorted(tag_distribution.items())),
            "preview": preview,
        }
        if args.commit:
            session.commit()
            result["backup_path"] = str(backup_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
