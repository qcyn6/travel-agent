from __future__ import annotations

import re
from datetime import date, timedelta


_WEEKDAYS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_RELATIVE_DAY_OFFSETS = (
    ("大后天", 3),
    ("后天", 2),
    ("明天", 1),
    ("明日", 1),
    ("今天", 0),
    ("今日", 0),
)
_WEEKDAY_PATTERN = re.compile(
    r"(?P<prefix>下下个|下下|下个|下|本|这|这个)?\s*(?:周|星期|礼拜)(?P<weekday>[一二三四五六日天])"
)
_WEEKEND_PATTERN = re.compile(r"(?P<prefix>下下个|下下|下个|下|本|这|这个)?\s*周末")


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _week_start(reference_date: date) -> date:
    return reference_date - timedelta(days=reference_date.weekday())


def _week_offset(prefix: str | None) -> int | None:
    if prefix in {"下下", "下下个"}:
        return 2
    if prefix in {"下", "下个"}:
        return 1
    if prefix in {"本", "这", "这个"}:
        return 0
    return None


def resolve_start_date(text: str, reference_date: date | None = None) -> date | None:
    """Resolve explicit and common Chinese relative travel dates to a calendar date."""

    reference = reference_date or date.today()

    full_date = re.search(
        r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*(?:日|号)?",
        text,
    )
    if full_date:
        resolved = _valid_date(*(int(value) for value in full_date.groups()))
        if resolved:
            return resolved

    month_day = re.search(
        r"(?<!\d)(1[0-2]|0?[1-9])\s*[月\-/\.]\s*(3[01]|[12]\d|0?[1-9])\s*(?:日|号)?",
        text,
    )
    if month_day:
        month, day = (int(value) for value in month_day.groups())
        resolved = _valid_date(reference.year, month, day)
        if resolved and resolved < reference:
            resolved = _valid_date(reference.year + 1, month, day)
        if resolved:
            return resolved

    for phrase, offset in _RELATIVE_DAY_OFFSETS:
        if phrase in text:
            return reference + timedelta(days=offset)

    weekend = _WEEKEND_PATTERN.search(text)
    if weekend:
        prefix = weekend.group("prefix")
        offset = _week_offset(prefix)
        if offset is None:
            candidate = _week_start(reference) + timedelta(days=5)
            return candidate if candidate >= reference else candidate + timedelta(days=7)
        candidate = _week_start(reference) + timedelta(days=offset * 7 + 5)
        if prefix in {"本", "这", "这个"} and candidate < reference:
            candidate += timedelta(days=7)
        return candidate

    weekday = _WEEKDAY_PATTERN.search(text)
    if weekday:
        target_weekday = _WEEKDAYS[weekday.group("weekday")]
        prefix = weekday.group("prefix")
        offset = _week_offset(prefix)
        if offset is None:
            candidate = _week_start(reference) + timedelta(days=target_weekday)
            return candidate if candidate >= reference else candidate + timedelta(days=7)
        return _week_start(reference) + timedelta(days=offset * 7 + target_weekday)

    next_month = re.search(r"(?:下个|下|下一个)月(?:初|1号)?", text)
    if next_month:
        year = reference.year + (1 if reference.month == 12 else 0)
        month = 1 if reference.month == 12 else reference.month + 1
        return date(year, month, 1)

    return None


def format_date_zh(value: date) -> str:
    weekday = "一二三四五六日"[value.weekday()]
    return f"{value.year}年{value.month}月{value.day}日（周{weekday}）"


__all__ = ["format_date_zh", "resolve_start_date"]
