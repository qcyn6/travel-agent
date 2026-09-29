"""Canonical interest vocabulary shared by destinations and attractions.

The catalog intentionally keeps a small, stable set of product-facing tags.
City and province profiles can still contain local wording, but maintenance
maps those words to a canonical attraction tag before they reach matching.
"""

from __future__ import annotations

from collections.abc import Iterable


CANONICAL_TAGS: tuple[str, ...] = (
    "丹霞",
    "主题娱乐",
    "乡村",
    "亲子",
    "人文",
    "休闲",
    "冰雪",
    "博物馆",
    "历史文化",
    "古城",
    "古建",
    "古镇",
    "园林",
    "地质景观",
    "城市漫游",
    "夜游",
    "宗教文化",
    "山地",
    "岛屿",
    "工业遗产",
    "建筑",
    "影视文化",
    "摄影",
    "森林",
    "民俗",
    "民族文化",
    "水乡",
    "江河",
    "沙漠",
    "海滨",
    "温泉",
    "湖泊",
    "湿地",
    "溶洞",
    "漂流",
    "瀑布",
    "研学",
    "红色文化",
    "美食",
    "自然",
    "航天文化",
    "花卉",
    "茶文化",
    "草原",
    "边境风情",
    "滨水",
    "近代建筑",
    "酒文化",
    "雕塑艺术",
    "生态",
    "露营",
    "高原",
)

_CANONICAL = frozenset(CANONICAL_TAGS)

# The attraction catalogue keeps detailed tags nationwide, while the Yunnan
# cleanup deliberately stores a smaller product vocabulary.  Matching must
# use the same projection or a requirement such as ``古城`` can never match a
# Yunnan attraction stored as ``古镇``.  Keep this mapping here as the single
# source of truth for cross-catalogue matching; it does not rewrite display
# tags or add database fields.
COMPACT_TAG_MAPPING: dict[str, tuple[str, ...]] = {
    "摄影": ("摄影",),
    "人文": ("人文",),
    "自然": ("自然",),
    "休闲": ("休闲",),
    "历史文化": ("历史文化",),
    "民族文化": ("民族文化",),
    "城市漫游": ("城市漫游",),
    "湖泊": ("滨水",),
    "滨水": ("滨水",),
    "建筑": ("建筑",),
    "古镇": ("古镇",),
    "亲子": ("亲子",),
    "山地": ("山地",),
    "古城": ("古镇",),
    "乡村": ("乡村",),
    "研学": ("研学",),
    "古建": ("建筑",),
    "宗教文化": ("宗教文化",),
    "园林": ("生态",),
    "生态": ("生态",),
    "森林": ("生态",),
    "美食": ("美食",),
    "湿地": ("滨水", "生态"),
    "夜游": ("夜游",),
    "地质景观": ("地质景观",),
    "江河": ("滨水",),
    "花卉": ("生态",),
    "民俗": ("民族文化",),
    "茶文化": ("民族文化",),
    "高原": ("山地",),
    "主题娱乐": ("主题娱乐",),
    "博物馆": ("博物馆",),
    "边境风情": ("民族文化",),
    "冰雪": ("山地",),
    "温泉": ("温泉",),
    "瀑布": ("滨水",),
    "红色文化": ("历史文化",),
    "草原": ("自然",),
    "水乡": ("滨水",),
    "溶洞": ("地质景观",),
    "丹霞": ("地质景观",),
    "岛屿": ("滨水",),
    "影视文化": ("主题娱乐",),
    "近代建筑": ("建筑",),
    "雕塑艺术": ("人文",),
    "工业遗产": ("历史文化",),
    "航天文化": ("研学",),
    "酒文化": ("美食",),
    "漂流": ("滨水",),
    "海滨": ("滨水",),
    "露营": ("休闲",),
}

# Six user-facing interest groups. Attraction rows keep their detailed tags,
# but retrieval and evaluation use these deterministic relations so the same
# attraction is never accepted by one stage and rejected by another.
INTEREST_TAG_GROUPS: dict[str, tuple[str, ...]] = {
    "古城": ("古城", "古镇", "历史文化", "人文", "建筑", "城市漫游", "博物馆"),
    "自然": ("自然", "山地", "生态", "森林", "滨水", "地质景观"),
    "历史文化": ("历史文化", "博物馆", "古建", "人文", "宗教文化"),
    "摄影": ("摄影", "自然", "建筑", "滨水", "夜游"),
    "休闲": ("休闲", "城市漫游", "滨水", "生态", "温泉"),
    "美食": ("美食", "古镇", "城市漫游", "夜游"),
}

_INTEREST_CATEGORY_ALIASES: dict[str, str] = {
    "古镇": "古城",
    "自然风光": "自然",
    "山水": "自然",
    "风景": "自然",
    "历史": "历史文化",
    "文化": "历史文化",
    "人文": "历史文化",
    "拍照": "摄影",
    "出片": "摄影",
    "慢游": "休闲",
    "慢慢玩": "休闲",
    "休闲慢游": "休闲",
    "吃": "美食",
    "小吃": "美食",
}

# Exact aliases cover the common city-profile vocabulary.  The keyword rules
# below handle the long tail of local themes without keeping a second tag
# vocabulary in the database.
_ALIASES: dict[str, str] = {
    "自然风光": "自然",
    "山水": "自然",
    "风景": "自然",
    "生态旅游": "生态",
    "滨水景观": "滨水",
    "水景": "滨水",
    "海岛": "岛屿",
    "海岛生态": "岛屿",
    "主题乐园": "主题娱乐",
    "科技": "研学",
    "科技文化": "研学",
    "户外": "休闲",
    "养生": "休闲",
    "避暑": "休闲",
    "阳光康养": "休闲",
    "江南": "水乡",
    "古都": "古城",
    "古村": "古镇",
    "传统村落": "古镇",
    "历史街区": "城市漫游",
    "非遗": "民俗",
    "戏曲": "民俗",
    "戏剧": "民俗",
    "早茶": "美食",
    "小吃": "美食",
    "滑雪": "冰雪",
    "化石": "地质景观",
    "喀斯特": "地质景观",
    "火山地貌": "地质景观",
    "观鸟": "湿地",
    "热带雨林": "森林",
    "红树林": "湿地",
    "花海": "花卉",
    "牡丹": "花卉",
    "皇家园林": "园林",
    "佛教文化": "宗教文化",
    "道教文化": "宗教文化",
    "宗教": "宗教文化",
    "矿业遗产": "工业遗产",
    "石油文化": "工业遗产",
    "工业旅游": "工业遗产",
    "长城": "历史文化",
    "丝路": "历史文化",
    "三国文化": "历史文化",
    "三星堆": "历史文化",
    "古蜀文化": "历史文化",
    "秦汉文化": "历史文化",
    "红色": "红色文化",
    "民族风情": "民族文化",
    "民族文化": "民族文化",
}

_NATURE_MARKERS = (
    "自然",
    "山水",
    "山海",
    "生态",
    "森林",
    "湿地",
    "湖",
    "河",
    "江",
    "海",
    "岛",
    "湾",
    "泉",
    "瀑布",
    "峡谷",
    "草原",
    "沙漠",
    "戈壁",
    "高原",
    "雪山",
    "热带",
    "花海",
    "梯田",
    "观鸟",
    "喀斯特",
    "盐湖",
)
_CULTURE_MARKERS = (
    "历史",
    "文化",
    "古",
    "遗址",
    "遗产",
    "文明",
    "博物馆",
    "书院",
    "寺",
    "庙",
    "塔",
    "窟",
    "非遗",
    "戏曲",
    "民俗",
    "民族",
    "故居",
    "红色",
    "宗教",
    "考古",
)


def canonicalize_tag(value: str | None, *, fallback: str | None = None) -> str | None:
    """Return a canonical tag, preserving unknown user input when requested."""

    tag = str(value or "").strip()
    if not tag:
        return None
    if tag in _CANONICAL:
        return tag
    if tag in _ALIASES:
        return _ALIASES[tag]

    # Prefer exact canonical substrings for compound local themes.
    for candidate in sorted(_CANONICAL, key=len, reverse=True):
        if candidate in tag:
            return candidate
    if any(marker in tag for marker in _NATURE_MARKERS):
        return "自然"
    if any(marker in tag for marker in _CULTURE_MARKERS):
        return "历史文化"
    if any(marker in tag for marker in ("休闲", "康养", "养生", "度假", "温泉")):
        return "休闲"
    if any(marker in tag for marker in ("美食", "饮食", "小吃", "餐饮")):
        return "美食"
    return fallback


def canonicalize_tags(
    values: Iterable[str] | None,
    *,
    fallback: str | None = "人文",
) -> list[str]:
    result: list[str] = []
    for value in values or []:
        tag = canonicalize_tag(value, fallback=fallback)
        if tag and tag not in result:
            result.append(tag)
    return result


def is_canonical_tag(value: str | None) -> bool:
    return str(value or "").strip() in _CANONICAL


def compact_tag_keys(value: str | None) -> frozenset[str]:
    """Return the compact semantic keys used by deterministic tag matching."""

    tag = canonicalize_tag(value, fallback=None)
    if tag is None:
        return frozenset()
    return frozenset(COMPACT_TAG_MAPPING.get(tag, (tag,)))


def canonicalize_interest(value: str | None, *, fallback: str | None = None) -> str | None:
    """Map user language to one of the six public interest categories."""

    interest = str(value or "").strip()
    if not interest:
        return None
    if interest in INTEREST_TAG_GROUPS:
        return interest
    return _INTEREST_CATEGORY_ALIASES.get(interest, fallback)


def interest_tag_keys(value: str | None) -> frozenset[str]:
    """Return compact attraction-tag keys accepted by one user interest."""

    interest = canonicalize_interest(value, fallback=None)
    if interest is None:
        return compact_tag_keys(value)
    return frozenset(
        key
        for tag in INTEREST_TAG_GROUPS[interest]
        for key in compact_tag_keys(tag)
    )


__all__ = [
    "CANONICAL_TAGS",
    "COMPACT_TAG_MAPPING",
    "INTEREST_TAG_GROUPS",
    "canonicalize_tag",
    "canonicalize_tags",
    "canonicalize_interest",
    "compact_tag_keys",
    "interest_tag_keys",
    "is_canonical_tag",
]
