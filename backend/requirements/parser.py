import re
from datetime import date

from backend.database.catalog import CITY_ALIASES, PROVINCE_ALIASES
from backend.models.requirement import (
    MAX_SINGLE_CITY_DAYS,
    MAX_TRAVEL_DAYS,
    MIN_TRAVEL_DAYS,
    TravelRequirement,
    scope_day_boundary_error,
)
from backend.requirements.dates import resolve_start_date
from backend.time_utils import extract_daily_time_window
from backend.tag_vocabulary import canonicalize_interest


_INTEREST_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("历史文化", ("历史文化", "历史", "文化", "人文")),
    ("自然", ("自然", "自然风光", "山水", "风景")),
    ("古城", ("古城", "古镇")),
    ("摄影", ("摄影", "拍照", "出片")),
    ("美食", ("美食", "吃", "小吃")),
    ("休闲", ("休闲", "慢慢玩", "慢游")),
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_PROVINCE_SUFFIXES = ("省", "自治区", "特别行政区")
_CITY_PREFERENCE_CONFIRMATION_MARKERS = (
    "没有指定城市",
    "没有特别想去的城市",
    "没有想去的城市",
    "不指定城市",
    "不想指定城市",
    "不挑城市",
    "你来推荐",
    "由你推荐",
    "帮我推荐一个城市",
    "推荐一个城市",
    "城市都可以",
    "城市随便",
    "随便安排城市",
)
_PROVINCE_PREFERENCE_CONFIRMATION_MARKERS = (
    "没有指定省份",
    "没有特别想去的省份",
    "没有想去的省份",
    "不指定省份",
    "不想指定省份",
    "不挑省份",
    "你来推荐一个省份",
    "由你推荐一个省份",
    "帮我推荐一个省份",
    "推荐一个省份",
    "省份都可以",
    "省份随便",
    "随便安排省份",
)


class RequirementBoundaryError(ValueError):
    """Raised when a user input falls outside the supported planning boundary."""


def _find_alias_matches(query: str, aliases: dict[str, object]) -> list[tuple[int, int, str, object]]:
    matches: list[tuple[int, int, str, object]] = []
    for alias, value in sorted(aliases.items(), key=lambda item: (-len(item[0]), item[0])):
        for match in re.finditer(re.escape(alias), query):
            matches.append((match.start(), match.end(), alias, value))

    selected: list[tuple[int, int, str, object]] = []
    for candidate in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(candidate[0] < item[1] and item[0] < candidate[1] for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item[0])


def _preferred_city_alias(city_name: str) -> str:
    aliases = [alias for alias, (canonical, _) in CITY_ALIASES.items() if canonical == city_name]
    return min(aliases, key=len) if aliases else city_name


def canonicalize_interests(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        canonical = canonicalize_interest(normalized, fallback=None) or next(
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
    if not value or any(char not in _CHINESE_DIGITS and char not in _CHINESE_UNITS for char in value):
        return None

    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS[char]
        if unit >= 10000:
            section += number
            total += section * unit
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number


class RequirementParser:
    """Deterministic M1 parser; an LLM adapter can later implement the same contract."""

    def parse(
        self,
        text: str,
        *,
        reference_date: date | None = None,
        strict_days: bool = True,
        strict_dates: bool = True,
    ) -> TravelRequirement:
        query = text.strip()
        if not query:
            raise ValueError("旅游需求不能为空")

        province_matches = _find_alias_matches(query, PROVINCE_ALIASES)
        provinces = [value for _, _, _, value in province_matches]
        city_matches = _find_alias_matches(query, CITY_ALIASES)
        cities: list[str] = []
        for start, end, alias, value in city_matches:
            city_name, city_province = value
            province_for_alias = PROVINCE_ALIASES.get(alias)
            if province_for_alias and query[end:].startswith(_PROVINCE_SUFFIXES):
                continue
            if province_for_alias and province_for_alias != city_province and province_for_alias in provinces:
                continue
            display_name = _preferred_city_alias(city_name)
            if display_name not in cities:
                cities.append(display_name)
        interests = [canonical for canonical, aliases in _INTEREST_ALIASES if any(alias in query for alias in aliases)]

        days = None
        day_pattern = re.compile(
            r"([0-9一二两三四五六七八九十百千万亿零〇]{1,16})\s*(?:天|日)(?:游)?"
        )
        for day_match in day_pattern.finditer(query):
            # Do not treat the day part of “9月20日” or “2026-10-03日” as
            # the requested trip length. Continue so “9月20日去杭州玩3天”
            # still extracts the later 3天.
            date_prefix = query[: day_match.start()].rstrip()
            if date_prefix.endswith(("月", "-", "/", ".")):
                continue
            days = _parse_chinese_number(day_match.group(1))
            break

        if strict_days and days is not None and not MIN_TRAVEL_DAYS <= days <= MAX_TRAVEL_DAYS:
            raise RequirementBoundaryError(
                f"单次旅行目前支持{MIN_TRAVEL_DAYS}～{MAX_TRAVEL_DAYS}天，请重新输入"
            )
        if not strict_days and days is not None and not MIN_TRAVEL_DAYS <= days <= MAX_TRAVEL_DAYS:
            days = None

        start_date = resolve_start_date(query, reference_date=reference_date)
        daily_start_time, daily_end_time = extract_daily_time_window(query)
        effective_reference_date = reference_date or date.today()
        if strict_dates and start_date is not None and start_date <= effective_reference_date:
            raise RequirementBoundaryError("出发日期必须从明天开始，请重新选择明天或之后的日期")

        if len(cities) > 1 or any(
            word in query for word in ("多个城市", "多城市", "同省多城市", "两三个城市", "2个城市", "3个城市")
        ):
            scope = "multi_city"
        elif len(cities) == 1 or any(word in query for word in ("一个城市", "只在一个城市", "单城市")):
            scope = "single_city"
        elif days is not None and days > MAX_SINGLE_CITY_DAYS:
            # Four to seven days are always a multi-city conversation when
            # the user has not explicitly named a single city. Do not leave
            # this as ``auto`` and let the responder offer an invalid option.
            scope = "multi_city"
        else:
            scope = "auto"

        boundary_error = scope_day_boundary_error(
            days,
            scope,
            city_count=len(cities),
        )
        if boundary_error:
            raise RequirementBoundaryError(boundary_error)

        compact_query = re.sub(r"[，。！？、\s]+", "", query)
        preference_markers = (
            "轻松",
            "慢一点",
            "慢慢",
            "紧凑",
            "不赶",
            "适合拍照",
            "喜欢拍照",
            "深度体验",
            "沉浸体验",
            "优先游览",
            "代表性景点",
        )
        preference_text = query if any(marker in query for marker in preference_markers) else None
        city_preference_confirmed = bool(cities) or any(
            marker in compact_query for marker in _CITY_PREFERENCE_CONFIRMATION_MARKERS
        ) or compact_query in {"没有", "无", "都行", "都可以", "你定"}
        province_preference_confirmed = any(
            marker in compact_query for marker in _PROVINCE_PREFERENCE_CONFIRMATION_MARKERS
        )

        return TravelRequirement.model_validate(
            {
                "province": provinces[0] if provinces else None,
                "cities": cities,
                "province_preference_confirmed": province_preference_confirmed,
                "city_preference_confirmed": city_preference_confirmed,
                "days": days,
                "interests": interests,
                "preference_text": preference_text,
                "travel_scope": scope,
                "start_date": start_date,
                "daily_start_time": daily_start_time,
                "daily_end_time": daily_end_time,
                # Route mode is deliberately not collected from the user. The
                # map tool chooses the best mode for each attraction segment.
                "transport_mode": "auto",
            },
            context={
                "allow_past_dates": not strict_dates,
                "reference_date": effective_reference_date,
            },
        )
