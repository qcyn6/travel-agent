import re
from datetime import date

from backend.models.requirement import TravelRequirement


_KNOWN_PROVINCES = ("浙江", "云南", "陕西")
_KNOWN_CITIES = ("杭州", "绍兴", "宁波", "昆明", "大理", "丽江", "西安", "宝鸡", "延安")
_INTEREST_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("历史文化", ("历史文化", "历史", "文化")),
    ("自然", ("自然", "山水", "风景")),
    ("古城", ("古城",)),
    ("古镇", ("古镇",)),
    ("摄影", ("摄影", "拍照", "出片")),
    ("人文", ("人文",)),
    ("美食", ("美食", "吃", "小吃")),
    ("休闲", ("休闲", "慢慢玩", "慢游")),
)
_CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def canonicalize_interests(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        canonical = next(
            (
                name
                for name, aliases in _INTEREST_ALIASES
                if normalized == name or any(alias == normalized for alias in aliases)
            ),
            normalized,
        )
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _parse_chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1)
        ones = _CHINESE_DIGITS.get(right, 0)
        return tens * 10 + ones
    return None


class RequirementParser:
    """Deterministic M1 parser; an LLM adapter can later implement the same contract."""

    def parse(self, text: str) -> TravelRequirement:
        query = text.strip()
        if not query:
            raise ValueError("旅游需求不能为空")

        provinces = [name for name in _KNOWN_PROVINCES if name in query]
        cities = [name for name in _KNOWN_CITIES if name in query]
        interests = [canonical for canonical, aliases in _INTEREST_ALIASES if any(alias in query for alias in aliases)]

        days = None
        day_match = re.search(r"([0-9一二两三四五六七八九十]{1,3})\s*(?:天|日)(?:游)?", query)
        if day_match:
            days = _parse_chinese_number(day_match.group(1))

        start_date = None
        date_match = re.search(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?", query)
        if date_match:
            try:
                start_date = date(*(int(value) for value in date_match.groups()))
            except ValueError:
                start_date = None

        if any(word in query for word in ("步行", "徒步")):
            transport_mode = "walking"
        elif any(word in query for word in ("公交", "公共交通", "地铁")):
            transport_mode = "transit"
        elif any(word in query for word in ("自驾", "驾车", "开车")):
            transport_mode = "driving"
        else:
            transport_mode = "auto"

        if len(cities) > 1 or any(word in query for word in ("多个城市", "两三个城市", "2个城市", "3个城市")):
            scope = "multi_city"
        elif len(cities) == 1 or any(word in query for word in ("一个城市", "只在一个城市", "单城市")):
            scope = "single_city"
        else:
            scope = "auto"

        preference_markers = ("轻松", "慢一点", "慢慢", "紧凑", "不赶", "适合拍照")
        preference_text = query if any(marker in query for marker in preference_markers) else None

        return TravelRequirement(
            province=f"{provinces[0]}省" if provinces else None,
            cities=cities,
            days=days,
            interests=interests,
            preference_text=preference_text,
            travel_scope=scope,
            start_date=start_date,
            transport_mode=transport_mode,
        )
