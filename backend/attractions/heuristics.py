"""Pure catalog heuristics shared by maintenance commands.

These estimates are not externally verified visit durations.
"""

from backend.attractions.descriptions import build_catalog_description


def estimate_duration(
    category: str,
    tags: list[str],
    name: str = "",
) -> int:
    """Estimate a visit window using category plus explicit name signals.

    The value remains marked as heuristic in the database.  The extra name
    signals prevent large outdoor sites and short urban stops from collapsing
    into the former 180/210-minute-only distribution.
    """

    text = f"{name} {category}".strip()
    if category == "主题娱乐":
        return 240
    if any(marker in text for marker in ("国家公园", "风景名胜", "大峡谷", "雪山", "长城")):
        return 240
    if any(marker in text for marker in ("漂流", "登山", "徒步", "森林公园", "野生动物")):
        return 210
    if category == "自然山水":
        return 180
    if category == "生态动植物":
        return 150
    if category in {"古城与街区", "古典园林"} or "古城" in tags or "古镇" in tags:
        return 180
    if category == "博物馆与展馆":
        return 120
    if category == "宗教建筑":
        return 90
    if category == "历史古迹" or "历史文化" in tags:
        return 120
    if category in {"滨海岛屿", "滨水景观"}:
        return 150
    if category == "城市公园":
        return 90
    if "美食" in tags:
        return 90
    if "休闲" in tags:
        return 150
    return 120


def build_description(
    *,
    city: str,
    name: str,
    category: str,
    tags: list[str],
    duration: int,
) -> str:
    return build_catalog_description(
        city=city,
        name=name,
        category=category,
        tags=tags,
        duration_minutes=duration,
    )
