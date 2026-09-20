"""Attraction catalog provenance and quality rules.

The catalog is intentionally curated without deleting source rows.  These
small, deterministic helpers make it possible to explain why a row is active,
under review, or excluded and to re-run curation after a future import.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


_NON_ATTRACTION_NAME = re.compile(
    r"(?:办事处|服务中心|管理中心|管理处|管理局|售票处|停车场|卫生间|游客中心|服务台|出入口|自行车租借站|三角锥)$"
)
_GENERIC_PLACE_SUFFIX = re.compile(r"(?:广场|公园)$")
_STATION_SQUARE = re.compile(
    r"(?:站|高铁|火车|客运|汽车).*(?:广场)$"
)
_PUBLIC_SPACE_SCENIC_MARKERS = (
    "景区",
    "风景",
    "国家",
    "森林",
    "湿地",
    "地质",
    "生态",
    "植物",
    "动物",
    "遗址",
    "文化",
    "主题",
    "游乐",
    "园林",
    "纪念",
    "故居",
    "海洋",
    "水上",
    "湖",
    "山",
    "河",
    "海",
    "花",
    "温泉",
    "奥林匹克",
)
_TEMPLATE_DESCRIPTION_MARKERS = (
    "行程中可以围绕",
    "建议预留约",
)
_DERIVED_DESCRIPTION_MARKERS = (
    "本地兴趣标签为",
    "开放时间、预约、交通和收费以实时信息为准",
)

SOURCE_RANK = {
    "amap": 3,
    "curated": 2,
    "demo": 1,
    "unknown": 0,
}


def infer_data_source(poi_id: str | None) -> str:
    """Infer the import family from the stable POI id convention."""

    value = str(poi_id or "").strip().casefold()
    if value.startswith("demo-"):
        return "demo"
    if value.startswith("mct-"):
        return "curated"
    if value.startswith("b"):
        return "amap"
    return "unknown"


def description_origin(description: str | None) -> str:
    value = str(description or "").strip()
    if not value:
        return "missing"
    if any(marker in value for marker in _TEMPLATE_DESCRIPTION_MARKERS):
        return "template"
    if all(marker in value for marker in _DERIVED_DESCRIPTION_MARKERS):
        return "derived"
    return "present"


def duration_origin(duration_minutes: int | None) -> str:
    """Mark the imported duration as a heuristic until provider facts exist."""

    return "missing" if not duration_minutes else "heuristic"


def is_non_attraction_name(name: str | None) -> bool:
    return bool(_NON_ATTRACTION_NAME.search(str(name or "").strip()))


def is_generic_place_name(name: str | None) -> bool:
    return bool(_GENERIC_PLACE_SUFFIX.search(str(name or "").strip()))


def public_space_kind(name: str | None, category: str | None = None) -> str | None:
    """Classify generic park/square names without rejecting real landmarks."""

    value = str(name or "").strip()
    if not is_generic_place_name(value):
        return None
    if value.endswith("广场"):
        return "facility_square" if _STATION_SQUARE.search(value) else "urban_square"
    if any(marker in value for marker in _PUBLIC_SPACE_SCENIC_MARKERS):
        return "scenic_park"
    if str(category or "").strip() not in {"城市公园", ""}:
        return "scenic_park"
    return "urban_park"


def has_valid_coordinates(longitude: float | None, latitude: float | None) -> bool:
    if longitude is None or latitude is None:
        return False
    try:
        return -180 <= float(longitude) <= 180 and -90 <= float(latitude) <= 90
    except (TypeError, ValueError):
        return False


def quality_flags(row: Mapping | object) -> list[str]:
    """Return stable, machine-readable flags for one attraction row."""

    get = row.get if isinstance(row, Mapping) else lambda key, default=None: getattr(row, key, default)
    flags: list[str] = []
    if is_non_attraction_name(get("name")):
        flags.append("non_attraction_name")
    else:
        space_kind = public_space_kind(get("name"), get("category"))
        if space_kind in {"urban_park", "urban_square"}:
            flags.append("urban_public_space")
        elif space_kind == "facility_square":
            flags.extend(("non_attraction_name", "transport_hub_square"))
    if description_origin(get("description")) == "template":
        flags.append("template_description")
    if infer_data_source(get("poi_id")) == "demo":
        flags.append("demo_record")
    if not has_valid_coordinates(get("longitude"), get("latitude")):
        flags.append("missing_coordinates")
    return flags


def duplicate_winner_key(row: Mapping | object) -> tuple:
    """Sort key where the first row is the preferred duplicate to keep."""

    get = row.get if isinstance(row, Mapping) else lambda key, default=None: getattr(row, key, default)
    flags = quality_flags(row)
    return (
        "non_attraction_name" not in flags,
        SOURCE_RANK[infer_data_source(get("poi_id"))],
        has_valid_coordinates(get("longitude"), get("latitude")),
        bool(str(get("description") or "").strip()),
        -int(get("id") or 0),
    )


__all__ = [
    "SOURCE_RANK",
    "description_origin",
    "duplicate_winner_key",
    "duration_origin",
    "has_valid_coordinates",
    "infer_data_source",
    "is_generic_place_name",
    "is_non_attraction_name",
    "public_space_kind",
    "quality_flags",
]
