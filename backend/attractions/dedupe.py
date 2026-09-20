"""Stable identity helpers for attraction candidates.

Provider search, the local catalog, and the vector index can all describe the
same place with slightly different records.  Planning should keep the first
record in ranking order and discard later records that have the same POI id or
the same normalized display name.
"""

import re
from collections.abc import Sequence
from typing import TypeVar


T = TypeVar("T")
_NAME_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)


def normalize_attraction_name(name: str) -> str:
    """Normalize harmless spacing and punctuation differences in a POI name."""

    return _NAME_SEPARATORS.sub("", name.strip().casefold())


def dedupe_attractions(items: Sequence[T]) -> list[T]:
    """Return items once, preserving their incoming ranking order.

    A stable ``poi_id`` is preferred when present, while a normalized name is
    also checked because provider/local records sometimes use different ids
    for the same visible attraction.
    """

    result: list[T] = []
    seen_poi_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in items:
        poi_id = str(getattr(item, "poi_id", "") or "").strip().casefold()
        name = normalize_attraction_name(str(getattr(item, "name", "") or ""))
        if (poi_id and poi_id in seen_poi_ids) or (name and name in seen_names):
            continue
        if poi_id:
            seen_poi_ids.add(poi_id)
        if name:
            seen_names.add(name)
        result.append(item)
    return result


__all__ = ["dedupe_attractions", "normalize_attraction_name"]
