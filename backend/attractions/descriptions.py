"""Deterministic, source-transparent descriptions for catalog records.

These descriptions are intentionally limited to fields already present in the
local catalog.  They improve retrieval without inventing opening hours,
ratings, history, or other facts that should come from a provider later.
"""

from __future__ import annotations

from collections.abc import Sequence


def build_catalog_description(
    *,
    city: str,
    name: str,
    category: str | None,
    tags: Sequence[str] | None,
    duration_minutes: int,
) -> str:
    clean_tags = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
    category_text = str(category or "景点").strip() or "景点"
    tag_text = "、".join(clean_tags[:5]) or category_text

    if any(tag in clean_tags for tag in ("历史文化", "人文", "古镇", "古建")):
        focus = "历史文化、人文观察和城市漫游"
    elif any(tag in clean_tags for tag in ("自然", "山地", "森林", "湿地", "滨水")):
        focus = "自然观赏、户外步行和摄影"
    elif any(tag in clean_tags for tag in ("美食", "夜游")):
        focus = "街区探索、地方风味和轻松漫步"
    elif any(tag in clean_tags for tag in ("休闲", "城市漫游")):
        focus = "城市休闲、短时游览和拍照"
    else:
        focus = "主题体验和灵活游览"

    return (
        f"{city}的{name}归入“{category_text}”，本地兴趣标签为{tag_text}。"
        f"适合{focus}；规划时长按{int(duration_minutes)}分钟估算，"
        "开放时间、预约、交通和收费以实时信息为准。"
    )


__all__ = ["build_catalog_description"]
