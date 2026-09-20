from __future__ import annotations

import re


_DIGITS = {
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
_TIME_TOKEN = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上|夜里|夜间)?\s*"
    r"(?P<hour>[0-9一二两三四五六七八九十]{1,2})\s*"
    r"(?:(?P<separator>[:：]|点|时)\s*"
    r"(?P<minute>[0-9零〇一二两三四五六七八九十]{1,2})?\s*分?)"
)


def normalize_clock_time(value: object, *, label: str = "时间") -> object:
    """Normalize a same-day clock value to HH:MM for model fields."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}必须使用HH:MM格式")
    text = value.strip()
    match = re.fullmatch(r"([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})", text)
    if not match:
        raise ValueError(f"{label}必须使用HH:MM格式")
    hour, minute = int(match.group(1)), int(match.group(2))
    _validate_clock(hour, minute, label)
    return f"{hour:02d}:{minute:02d}"


def extract_daily_time_window(text: str) -> tuple[str | None, str | None]:
    """Extract a same-day start/end window from natural-language input."""

    matches = []
    for match in _TIME_TOKEN.finditer(text):
        if not _is_time_expression(text, match):
            continue
        hour = _parse_number(match.group("hour"))
        minute_text = match.group("minute")
        minute = _parse_number(minute_text) if minute_text else 0
        if hour is None or minute is None:
            continue
        period = match.group("period") or ""
        if period in {"下午", "晚上", "夜里", "夜间"} and hour < 12:
            hour += 12
        if period == "凌晨" and hour == 12:
            hour = 0
        try:
            _validate_clock(hour, minute, "每日时间")
        except ValueError:
            continue
        matches.append((f"{hour:02d}:{minute:02d}", match.start(), match.end()))

    if not matches:
        return None, None

    start: str | None = None
    end: str | None = None
    unlabelled: list[str] = []
    for value, match_start, match_end in matches:
        before = text[max(0, match_start - 6):match_start]
        after = text[match_end:min(len(text), match_end + 6)]
        context = before + after
        if any(marker in context for marker in ("结束", "截止", "最晚", "到此")):
            if end is None:
                end = value
            else:
                unlabelled.append(value)
        elif any(marker in context for marker in ("开始", "起始", "从", "起")):
            if start is None:
                start = value
            else:
                unlabelled.append(value)
        else:
            unlabelled.append(value)

    if start is None and unlabelled:
        start = unlabelled.pop(0)
    if end is None and unlabelled:
        end = unlabelled.pop(0)
    return start, end


def _is_time_expression(text: str, match: re.Match[str]) -> bool:
    """Reject ordinary wording such as ``轻松一点`` from clock parsing.

    A colon or a day-period word is unambiguously a clock. Chinese ``点/时``
    expressions additionally need an adjacent time-window marker; otherwise
    words like ``一点``, ``有点`` and ``差一点`` would become 01:00.
    """

    if match.group("period") or match.group("separator") in {":", "："}:
        return True
    before = text[max(0, match.start() - 8):match.start()].rstrip()
    after = text[match.end():min(len(text), match.end() + 8)].lstrip()
    return before.endswith(("每天", "每日", "从", "自", "于", "到", "至")) or after.startswith(
        ("开始", "结束", "截止", "之前", "以后", "出发", "到", "至", "起")
    )


def _parse_number(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if len(value) == 2 and value[0] == "十" and value[1] in _DIGITS:
        return 10 + _DIGITS[value[1]]
    if len(value) == 2 and value[1] == "十" and value[0] in _DIGITS:
        return _DIGITS[value[0]] * 10
    if len(value) == 3 and value[1] == "十" and value[0] in _DIGITS and value[2] in _DIGITS:
        return _DIGITS[value[0]] * 10 + _DIGITS[value[2]]
    if all(char in _DIGITS for char in value):
        number = 0
        for char in value:
            number = number * 10 + _DIGITS[char]
        return number
    return None


def _validate_clock(hour: int, minute: int, label: str) -> None:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{label}必须是有效的24小时制时间")
