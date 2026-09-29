from __future__ import annotations

import re
from datetime import date, timedelta

from backend.requirements.dates import resolve_start_date


_WEATHER_DAY_NUMBERS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_RELATIVE_WEATHER_DATES = {
    "大后天": 3,
    "后天": 2,
    "明天": 1,
    "明日": 1,
    "今天": 0,
    "今日": 0,
}


def requested_weather_dates(message: str, today: date) -> list[date] | None:
    """Extract explicit weather dates or a forward-looking day range.

    A phrase such as ``未来三天`` means the three days after today by default.
    The current day is included only when the user explicitly asks for it.
    """

    normalized = re.sub(r"\s+", "", message)
    range_match = re.search(
        r"(?:未来|接下来|接下来的|往后)(?:的)?([0-9一二两三四五六七八九十]+)天",
        normalized,
    )
    if range_match:
        raw_count = range_match.group(1)
        count = int(raw_count) if raw_count.isdigit() else _WEATHER_DAY_NUMBERS.get(raw_count)
        if count and 1 <= count <= 10:
            explicitly_excludes_today = any(
                marker in normalized
                for marker in ("不包括今天", "不含今天", "不算今天", "不包括今日", "不含今日")
            )
            explicitly_includes_today = any(
                marker in normalized
                for marker in (
                    "包括今天",
                    "含今天",
                    "算上今天",
                    "今天也算",
                    "算今天",
                    "包括今日",
                    "含今日",
                    "算上今日",
                    "今日也算",
                    "算今日",
                    "从今天",
                    "今天起",
                    "今天开始",
                    "从今日",
                    "今日起",
                )
            )
            start = (
                today
                if explicitly_includes_today and not explicitly_excludes_today
                else today + timedelta(days=1)
            )
            return [start + timedelta(days=offset) for offset in range(count)]

    explicit_dates: set[date] = set()
    for match in re.finditer(
        r"大后天|后天|明天|明日|今天|今日",
        normalized,
    ):
        explicit_dates.add(today + timedelta(days=_RELATIVE_WEATHER_DATES[match.group()]))

    for match in re.finditer(
        r"(?:(20\d{2})年)?(1[0-2]|0?[1-9])月(3[01]|[12]\d|0?[1-9])(?:日|号)?",
        normalized,
    ):
        year = int(match.group(1) or today.year)
        try:
            candidate = date(year, int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if match.group(1) is None and candidate < today:
            candidate = candidate.replace(year=today.year + 1)
        explicit_dates.add(candidate)

    return sorted(explicit_dates) or None


def resolve_weather_dates(message: str, today: date) -> list[date]:
    """Resolve requested forecast dates, defaulting to a single current day."""

    dates = requested_weather_dates(message, today)
    if dates:
        return dates
    return [resolve_start_date(message, reference_date=today) or today]


__all__ = ["requested_weather_dates", "resolve_weather_dates"]
