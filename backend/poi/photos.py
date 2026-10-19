"""Shared normalization for display-only POI photo URLs."""

from collections.abc import Mapping
from urllib.parse import urlparse


def safe_photo_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    parsed = urlparse(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else None


def first_photo_url(value: object) -> str | None:
    if isinstance(value, Mapping):
        return safe_photo_url(value.get("url"))
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, Mapping):
            url = safe_photo_url(item.get("url"))
            if url:
                return url
    return None
