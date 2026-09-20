from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from backend.models.general_chat import GeneralIntentDecision
from backend.requirements.dates import resolve_start_date


_ROUTE_MARKERS = ("怎么走", "怎么去", "路线", "导航", "多远", "多久", "交通")
_WEATHER_MARKERS = (
    "天气",
    "气温",
    "温度",
    "下雨",
    "降雨",
    "降水",
    "冷不冷",
    "热不热",
    "适合出门",
)
_KNOWLEDGE_MARKERS = (
    "介绍",
    "有哪些",
    "有什么",
    "特色",
    "特点",
    "值得",
    "适合",
    "了解",
    "推荐",
    "风景",
    "美食",
    "历史",
    "文化",
    "自然",
    "人文",
    "好玩吗",
)
_ROUTE_ENDINGS = (
    "怎么走",
    "怎么去",
    "需要多久",
    "要多久",
    "大概多久",
    "多远",
    "的路线",
    "路线",
    "导航",
)


class GeneralIntentRouter:
    """Deterministic ordinary-chat intent and parameter extraction."""

    def __init__(self, *, today_provider=date.today):
        self.today_provider = today_provider

    def classify(
        self,
        message: str,
        *,
        parsed=None,
        recent_messages: Sequence[dict[str, str]] = (),
        planning_signal: bool = False,
    ) -> GeneralIntentDecision:
        clean = message.strip()
        pending = self._resume_pending(clean, recent_messages)
        if pending is not None:
            return pending

        normalized = _compact(clean)
        if self._looks_like_route(normalized):
            origin, destination = _extract_route_places(clean)
            missing = []
            if not origin:
                missing.append("origin")
            if not destination:
                missing.append("destination")
            return GeneralIntentDecision(
                intent="route",
                origin=origin,
                destination=destination,
                route_mode=_route_mode(normalized),
                missing_fields=missing,
            )

        if any(marker in normalized for marker in _WEATHER_MARKERS):
            city = _parsed_city(parsed) or _extract_weather_city(clean)
            return GeneralIntentDecision(
                intent="weather",
                city=city,
                target_date=resolve_start_date(clean, reference_date=self.today_provider())
                or self.today_provider(),
                missing_fields=[] if city else ["city"],
            )

        if planning_signal:
            return GeneralIntentDecision(intent="planning")

        if any(marker in normalized for marker in _KNOWLEDGE_MARKERS) or _has_destination(parsed):
            return GeneralIntentDecision(intent="knowledge")
        return GeneralIntentDecision(intent="general")

    def _resume_pending(
        self,
        message: str,
        recent_messages: Sequence[dict[str, str]],
    ) -> GeneralIntentDecision | None:
        if not recent_messages:
            return None
        last = recent_messages[-1]
        if last.get("role") != "assistant":
            return None
        assistant = str(last.get("content", ""))
        previous_user = next(
            (
                str(item.get("content", ""))
                for item in reversed(recent_messages[:-1])
                if item.get("role") == "user"
            ),
            "",
        )
        if "还需要出发地点" in assistant:
            _, destination = _extract_route_places(previous_user)
            return GeneralIntentDecision(
                intent="route",
                origin=_clean_place(message),
                destination=destination,
                route_mode=_route_mode(_compact(previous_user)),
                missing_fields=[] if destination else ["destination"],
            )
        if "还需要目的地" in assistant:
            origin, _ = _extract_route_places(previous_user)
            return GeneralIntentDecision(
                intent="route",
                origin=origin,
                destination=_clean_place(message),
                route_mode=_route_mode(_compact(previous_user)),
                missing_fields=[] if origin else ["origin"],
            )
        if "还需要查询天气的城市" in assistant:
            return GeneralIntentDecision(
                intent="weather",
                city=_clean_place(message),
                target_date=resolve_start_date(previous_user, reference_date=self.today_provider())
                or self.today_provider(),
            )
        return None

    @staticmethod
    def _looks_like_route(normalized: str) -> bool:
        if "从" in normalized and "到" in normalized:
            return True
        if any(marker in normalized for marker in _ROUTE_MARKERS if marker != "交通"):
            return True
        return "交通" in normalized and "到" in normalized


def _extract_route_places(message: str) -> tuple[str | None, str | None]:
    clean = message.strip().strip("，。！？,.!?；;：:")
    patterns = (
        r"(?:请问)?从(?P<origin>.+?)到(?P<destination>.+)",
        r"(?:请问)?(?P<origin>.+?)到(?P<destination>.+)",
    )
    for pattern in patterns:
        match = re.match(pattern, clean)
        if match:
            origin = _clean_place(match.group("origin"))
            destination = _clean_place(match.group("destination"))
            return origin or None, destination or None

    destination_match = re.match(r"(?:请问)?(?:我想)?去?(?P<destination>.+?)(?:怎么走|怎么去)$", clean)
    if destination_match:
        destination = _clean_place(destination_match.group("destination"))
        return None, destination or None
    return None, None


def _clean_place(value: str) -> str:
    result = value.strip().strip("，。！？,.!?；;：:")
    result = re.sub(r"^(?:请问|我想|想要|想|去)", "", result)
    marker_positions = [result.find(ending) for ending in _ROUTE_ENDINGS if ending in result]
    if marker_positions:
        result = result[: min(marker_positions)]
    elif result.endswith("交通"):
        result = result[:-2]
    return result.strip().strip("，。！？,.!?；;：:")


def _extract_weather_city(message: str) -> str | None:
    prefix = re.split("|".join(re.escape(marker) for marker in _WEATHER_MARKERS), message, maxsplit=1)[0]
    prefix = re.sub(
        r"(?:请问|帮我查一下|帮我查|查一下|看看|今天|今日|明天|明日|后天|大后天|这周|本周|下周|周[一二三四五六日天]|星期[一二三四五六日天]|\d{4}[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}[日号]?|\d{1,2}月\d{1,2}[日号]?)",
        "",
        prefix,
    )
    city = prefix.strip().strip("的，。！？,.!?；;：:")
    return city or None


def _route_mode(normalized: str):
    if any(marker in normalized for marker in ("步行", "走路", "走过去")):
        return "walking"
    if any(marker in normalized for marker in ("开车", "驾车", "自驾")):
        return "driving"
    return "transit"


def _parsed_city(parsed) -> str | None:
    cities = getattr(parsed, "cities", None) or []
    return cities[0] if cities else None


def _has_destination(parsed) -> bool:
    return bool(getattr(parsed, "province", None) or (getattr(parsed, "cities", None) or []))


def _compact(value: str) -> str:
    return re.sub(r"[，。！？、,.!?；;：:\s]", "", value).lower()


__all__ = ["GeneralIntentRouter"]
