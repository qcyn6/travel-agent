"""Conservative parsing for Amap POI weekly opening hours."""

from __future__ import annotations

import re
from datetime import date


_TIME_RANGE = re.compile(
    r"(?<!\d)(\d{1,2}:\d{2})\s*(?:-|–|—|~|～|至|到)\s*(\d{1,2}:\d{2})(?!\d)"
)
_WEEKDAY = re.compile(r"(?:星期|周)([一二三四五六日天])")
_WEEKDAY_NUMBER = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_HOLIDAY_CAVEATS = ("法定节假日除外", "节假日除外", "节假日另行通知")


def opening_intervals_for_date(
    weekly_hours: str | None,
    visit_date: date | None,
) -> list[tuple[int, int]] | None:
    """Return trustworthy intervals for a weekday, or ``None`` if unknown.

    ``[]`` means the source explicitly says the attraction is closed. A ``None``
    result is deliberately unconstrained: the source was absent, ambiguous, or
    could not safely be applied to this date.
    """

    if not isinstance(weekly_hours, str) or not weekly_hours.strip():
        return None
    text = weekly_hours.strip()
    if any(note in text for note in _HOLIDAY_CAVEATS):
        # The weekday schedule explicitly excludes some holidays. Without a
        # holiday calendar/provider, do not turn that exception into a false
        # closed-hours constraint.
        return None

    schedule = _parse_weekly_schedule(text)
    if schedule is None:
        return None
    if visit_date is not None:
        return schedule[visit_date.weekday()]

    # Without a departure date, only apply a schedule if it is identical and
    # known for every weekday.
    first = schedule[0]
    if first is not None and all(intervals == first for intervals in schedule):
        return first
    return None


def visit_fits_opening_hours(
    weekly_hours: str | None,
    visit_date: date | None,
    start_minute: int,
    visit_minutes: int,
) -> bool:
    """Return whether a full visit fits if it starts at the planned time.

    No rescheduling or waiting is performed. Unknown hours never constrain
    planning; known hours must contain the entire visit at ``start_minute``.
    """

    intervals = opening_intervals_for_date(weekly_hours, visit_date)
    if intervals is None:
        return True
    for open_minute, close_minute in intervals:
        if open_minute <= start_minute and start_minute + visit_minutes <= close_minute:
            return True
    return False


def _parse_weekly_schedule(
    text: str,
) -> list[list[tuple[int, int]] | None] | None:
    if any(marker in text for marker in ("全天开放", "全天营业", "全天", "24小时营业")):
        all_day = [(0, 24 * 60)]
        return [all_day for _ in range(7)]

    schedule: list[list[tuple[int, int]] | None] = [None] * 7
    has_weekday_segments = False
    general_intervals: list[tuple[int, int]] = []

    for segment in re.split(r"[;；\n]+", text):
        segment = segment.strip()
        if not segment:
            continue
        label = re.split(r"[:：]", segment, maxsplit=1)[0]
        weekdays = _weekday_set(label)
        intervals = _parse_intervals(segment)

        if intervals and any(
            marker in segment for marker in ("休息", "闭馆", "不开放", "关闭")
        ):
            # Do not silently apply a broad interval when the same segment
            # also contains an exception such as "周三闭馆".
            return None

        if weekdays is None:
            if not has_weekday_segments:
                general_intervals.extend(intervals)
            continue

        has_weekday_segments = True
        if intervals:
            for weekday in weekdays:
                current = schedule[weekday] or []
                schedule[weekday] = _merge_intervals([*current, *intervals])
        elif any(marker in segment for marker in ("休息", "闭馆", "不开放", "关闭")):
            for weekday in weekdays:
                schedule[weekday] = []

    if not has_weekday_segments:
        intervals = _merge_intervals(general_intervals or _parse_intervals(text))
        return [intervals for _ in range(7)] if intervals else None

    return schedule


def _weekday_set(label: str) -> set[int] | None:
    matches = list(_WEEKDAY.finditer(label))
    if not matches:
        return None
    values = [_WEEKDAY_NUMBER[match.group(1)] for match in matches]
    if len(values) == 1:
        return {values[0]}
    if len(values) == 2:
        between = label[matches[0].end() : matches[1].start()]
        if any(token in between for token in ("至", "到", "-", "~", "～")):
            result = {values[0]}
            current = values[0]
            while current != values[1]:
                current = (current + 1) % 7
                result.add(current)
            return result
        if any(token in between for token in ("、", ",", "，", "/")):
            return set(values)
    # A more complex weekday label is ambiguous; leave it unconstrained.
    return None


def _parse_intervals(text: str) -> list[tuple[int, int]]:
    if any(marker in text for marker in ("全天开放", "全天营业", "全天", "24小时营业")):
        return [(0, 24 * 60)]
    intervals = []
    for match in _TIME_RANGE.finditer(text):
        start = _parse_clock(match.group(1))
        end = _parse_clock(match.group(2), allow_24=True)
        if start is not None and end is not None and end > start:
            intervals.append((start, end))
    return _merge_intervals(intervals)


def _parse_clock(value: str, *, allow_24: bool = False) -> int | None:
    hour_text, minute_text = value.split(":", 1)
    hour, minute = int(hour_text), int(minute_text)
    if minute > 59 or hour > 24 or (hour == 24 and (not allow_24 or minute != 0)):
        return None
    return hour * 60 + minute


def _merge_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


__all__ = ["opening_intervals_for_date", "visit_fits_opening_hours"]
