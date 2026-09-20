"""Shared deterministic tag matching for destination and attraction selection."""

from collections.abc import Iterable

from backend.tag_vocabulary import canonicalize_tag


# These aliases are only used to bridge ordinary user language to the
# catalogue vocabulary.  The value finally returned by
# ``normalize_interest_tags`` is always the exact tag string from the
# database; these are not a second source of truth for tags.
_INTEREST_ALIASES: dict[str, str] = {
    "自然风光": "自然",
    "山水": "自然",
    "风景": "自然",
    "生态": "自然",
    "历史": "历史文化",
    "文化": "历史文化",
    "古镇": "古城",
    "拍照": "摄影",
    "出片": "摄影",
    "吃": "美食",
    "小吃": "美食",
    "慢游": "休闲",
    "慢慢玩": "休闲",
}


def _clean_unique(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def normalize_interest_tags(
    interests: Iterable[str] | None,
    catalog_tags: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    """Map interests to exact province/city catalogue tags.

    Matching order is deliberately conservative:

    1. exact tag match;
    2. the existing language alias mapped to an exact catalogue tag;
    3. a unique containment match (for example ``自然风光`` -> ``自然``).

    Ambiguous or unknown values are returned separately instead of being
    copied into the requirement.  This keeps the requirement contract tied to
    the actual ``provinces.tags``/``cities.tags`` vocabulary.
    """

    tags = _clean_unique(catalog_tags)
    by_key = {tag.casefold(): tag for tag in tags}
    normalized: list[str] = []
    unresolved: list[str] = []

    for interest in _clean_unique(interests):
        direct = by_key.get(interest.casefold())
        if direct is not None:
            if direct not in normalized:
                normalized.append(direct)
            continue

        canonical = _INTEREST_ALIASES.get(interest, interest)
        canonical = canonicalize_tag(canonical, fallback=None) or canonical
        canonical_tag = by_key.get(canonical.casefold())
        if canonical_tag is not None:
            if canonical_tag not in normalized:
                normalized.append(canonical_tag)
            continue

        query = canonical.casefold()
        related = [
            tag
            for tag in tags
            if query in tag.casefold() or tag.casefold() in query
        ]
        if len(related) == 1:
            if related[0] not in normalized:
                normalized.append(related[0])
        else:
            unresolved.append(interest)

    return normalized, unresolved


def tag_match_score(tags: Iterable[str] | None, interests: Iterable[str] | None) -> int:
    """Count exact shared tags using the same rule for every catalog level."""

    tag_set = {str(tag).strip() for tag in (tags or []) if str(tag).strip()}
    interest_set = {
        str(interest).strip()
        for interest in (interests or [])
        if str(interest).strip()
    }
    return len(tag_set & interest_set)


__all__ = ["normalize_interest_tags", "tag_match_score"]
