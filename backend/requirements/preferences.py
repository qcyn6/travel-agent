"""Validation helpers for free-form travel preferences."""

from __future__ import annotations

from collections.abc import Iterable
import re


NON_PREFERENCE_ANSWERS = frozenset(
    {
        "没有",
        "无",
        "没有了",
        "没有额外体验偏好",
        "没有额外偏好",
        "没有偏好",
        "无额外偏好",
        "无特殊偏好",
        "随便",
        "都行",
        "都可以",
        "不知道",
        "没想法",
        "好的",
        "确认",
        "确认生成计划",
    }
)

INTEREST_ONLY_ANSWERS = frozenset(
    {
        "自然",
        "自然风光",
        "历史",
        "历史文化",
        "古城",
        "古镇",
        "摄影",
        "拍照",
        "美食",
        "休闲",
        "休闲慢游",
    }
)

_DATE_ONLY_PATTERN = re.compile(
    r"^(?:今天|明天|后天|大后天|本周末|这周末|下周[一二三四五六日天]?|"
    r"\d{1,2}月\d{1,2}日)(?:出发)?$"
)
_TIME_WINDOW_PATTERN = re.compile(
    r"^(?:每天)?\d{1,2}(?::\d{2}|点(?:半)?)?(?:到|至|—|-)"
    r"\d{1,2}(?::\d{2}|点(?:半)?)?$"
)


def normalize_preference_text(value: str | None) -> str:
    return re.sub(r"[，。！？、；：,.!?;:\s]", "", (value or "").strip())


def is_meaningful_preference_text(
    value: str | None,
    interests: Iterable[str] | None = None,
) -> bool:
    """Return whether text contains experience/style information beyond tags."""

    normalized = normalize_preference_text(value)
    if len(normalized) < 2:
        return False
    if normalized in NON_PREFERENCE_ANSWERS or normalized in INTEREST_ONLY_ANSWERS:
        return False
    normalized_interests = {
        normalize_preference_text(interest) for interest in (interests or [])
    }
    if normalized in normalized_interests:
        return False
    if _DATE_ONLY_PATTERN.fullmatch(normalized):
        return False
    if _TIME_WINDOW_PATTERN.fullmatch(normalized):
        return False
    return True


__all__ = ["is_meaningful_preference_text", "normalize_preference_text"]
