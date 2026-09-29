from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from backend.models.general_chat import GeneralIntentDecision
from backend.conversation.weather_dates import requested_weather_dates, resolve_weather_dates
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
_NEARBY_MARKERS = ("附近", "周边", "周围", "旁边", "一带")
_NEARBY_CATEGORY_ALIASES = (
    ("公共厕所", ("公共厕所", "厕所", "卫生间", "洗手间", "公厕")),
    ("停车场", ("停车场", "停车位", "停车")),
    ("地铁站", ("地铁站", "地铁口", "地铁")),
    ("公交站", ("公交车站", "公交站", "公交车", "公交")),
    ("药店", ("药店", "药房", "买药")),
    ("便利店", ("便利店", "超市")),
    ("餐饮服务", ("餐饮", "吃饭", "吃什么", "吃点", "好吃", "餐馆", "饭店", "餐厅", "美食", "吃的", "小吃")),
    ("医院", ("医院", "急诊")),
    ("加油站", ("加油站", "加油")),
    ("充电站", ("充电站", "充电桩", "充电")),
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
    "注意事项",
    "注意",
    "准备",
    "规划",
    "规则",
    "建议",
    "指南",
    "行前准备",
    "证件",
    "打包",
    "徒步",
    "登山",
    "海边",
    "海滨",
    "水上",
    "冬季",
    "寒冷",
    "高温",
    "防暑",
)
_WEATHER_KNOWLEDGE_MARKERS = (
    "介绍",
    "有哪些",
    "有什么",
    "特色",
    "特点",
    "值得",
    "推荐",
    "景点",
    "风景",
    "美食",
    "历史",
    "文化",
    "自然",
    "人文",
    "好玩吗",
    "适合拍照",
    "适合游玩",
    "适合旅行",
)
_RETRIEVAL_TOPIC_MARKERS = (
    "高原",
    "雨季",
    "亲子",
    "老人",
    "自驾",
    "慢节奏",
    "不赶时间",
    "摄影旅行",
    "历史文化旅行",
    "自然风光旅行",
    "美食旅行",
    "旅行注意事项",
    "行程规划",
    "主题旅行",
    "行前准备",
    "证件",
    "徒步",
    "登山",
    "海边",
    "海滨",
    "水上",
    "冬季",
    "寒冷",
    "高温",
    "防暑",
)
_TRAVEL_SCOPE_MARKERS = (
    "旅游", "旅行", "出游", "游玩", "行程", "攻略", "目的地", "省份", "城市",
    "景点", "景区", "古城", "古镇", "博物馆", "公园", "湖", "山", "寺", "故宫",
    "长城", "自然风光", "历史文化", "人文", "风景", "路线", "天气", "交通", "美食", "住宿",
    "徒步", "登山", "户外", "海边", "海滨", "水上", "冬季", "寒冷", "高温", "防暑",
    "行前准备", "出发前", "证件", "打包",
)
_CASUAL_MARKERS = (
    "你好", "嗨", "您好", "你是谁", "你能做什么", "你会做什么", "介绍一下你",
    "你有什么能力", "能帮我什么", "谢谢", "感谢",
)
_ROUTE_KNOWLEDGE_MARKERS = (
    "介绍",
    "有哪些",
    "有什么",
    "景点",
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
_CONTEXTUAL_FOLLOW_UP_STARTS = (
    "那里",
    "那边",
    "那个",
    "这个",
    "当地",
    "刚才",
    "你说的",
    "还有",
    "老人",
    "孩子",
    "具体",
    "展开",
    "为什么",
)
_NON_INHERITED_TOOL_FOLLOW_UPS = (
    "步行",
    "走路",
    "公交",
    "地铁",
    "开车",
    "驾车",
    "天气",
    "气温",
    "下雨",
)


class GeneralIntentRouter:
    """Deterministic ordinary-chat intent and parameter extraction."""

    def __init__(
        self,
        *,
        today_provider=date.today,
        retrieval_markers: Sequence[str] | None = None,
    ):
        self.today_provider = today_provider
        markers = (
            _RETRIEVAL_TOPIC_MARKERS
            if retrieval_markers is None
            else retrieval_markers
        )
        self.retrieval_markers = _unique_compact(markers)

    def classify(
        self,
        message: str,
        *,
        parsed=None,
        recent_messages: Sequence[dict[str, str]] = (),
        conversation_summary: str | None = None,
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
            route_has_knowledge = any(
                marker in normalized for marker in _ROUTE_KNOWLEDGE_MARKERS
            )
            return GeneralIntentDecision(
                intent="knowledge" if route_has_knowledge else "route",
                intents=["knowledge", "route"] if route_has_knowledge else ["route"],
                need_retrieval=_needs_knowledge_retrieval(
                    normalized, parsed, self.retrieval_markers
                ),
                origin=origin,
                destination=destination,
                route_mode=_route_mode(normalized),
                missing_fields=missing,
            )

        if _is_route_mode_follow_up(normalized):
            return GeneralIntentDecision(
                intent="route",
                intents=["route"],
                route_mode=_route_mode(normalized),
                missing_fields=["origin", "destination"],
            )

        if _is_weather_date_follow_up(normalized):
            city = _weather_context_city(recent_messages, conversation_summary)
            forecast_dates = _weather_dates_for_message(clean, self.today_provider())
            return GeneralIntentDecision(
                intent="weather",
                intents=["weather"],
                city=city,
                target_date=forecast_dates[0],
                forecast_dates=forecast_dates,
                missing_fields=[] if city else ["city"],
            )
        if _is_weather_location_follow_up(normalized):
            city = _weather_context_city(recent_messages, conversation_summary)
            forecast_dates = _weather_dates_for_message(
                clean,
                self.today_provider(),
                recent_messages=recent_messages,
                inherit_previous=True,
            )
            return GeneralIntentDecision(
                intent="weather",
                intents=["weather"],
                city=city,
                target_date=forecast_dates[0],
                forecast_dates=forecast_dates,
                missing_fields=[] if city else ["city"],
            )

        if _is_nearby_request(clean, normalized):
            center = _extract_nearby_center(clean)
            category = _extract_nearby_category(clean)
            missing = []
            if not center:
                missing.append("nearby_center")
            if not category:
                missing.append("nearby_category")
            return GeneralIntentDecision(
                intent="nearby",
                intents=["nearby"],
                nearby_center=center,
                nearby_category=category,
                missing_fields=missing,
            )

        contextual_follow_up = bool(
            is_contextual_follow_up(clean)
            and (
                _summary_has_tourism_context(conversation_summary or "")
                or _recent_history_has_tourism_context(recent_messages)
            )
        )
        tourism_signal = _is_tourism_message(normalized, parsed) or contextual_follow_up
        weather_signal = any(marker in normalized for marker in _WEATHER_MARKERS)
        if (
            weather_signal
            and _needs_knowledge_retrieval(
                normalized, parsed, self.retrieval_markers
            )
            and not _is_weather_lookup_request(clean, parsed)
        ):
            # "高温天气出游要注意什么" asks for stable travel guidance,
            # not a city-specific forecast. Keep it on the guide-RAG path.
            weather_signal = False
        if weather_signal:
            knowledge_signal = any(
                marker in normalized for marker in _WEATHER_KNOWLEDGE_MARKERS
            ) and tourism_signal
        else:
            knowledge_signal = (
                any(marker in normalized for marker in _KNOWLEDGE_MARKERS)
                and tourism_signal
            )
        if contextual_follow_up and not weather_signal:
            knowledge_signal = True

        if weather_signal:
            city = _parsed_city(parsed) or _extract_weather_city(clean)
            forecast_dates = _weather_dates_for_message(clean, self.today_provider())
            if knowledge_signal:
                return GeneralIntentDecision(
                    intent="knowledge",
                    intents=["knowledge", "weather"],
                    need_retrieval=_needs_knowledge_retrieval(
                        normalized, parsed, self.retrieval_markers
                    ),
                    city=city,
                    target_date=forecast_dates[0],
                    forecast_dates=forecast_dates,
                    missing_fields=[] if city else ["city"],
                )
            return GeneralIntentDecision(
                intent="weather",
                intents=["weather"],
                city=city,
                target_date=forecast_dates[0],
                forecast_dates=forecast_dates,
                missing_fields=[] if city else ["city"],
            )

        if planning_signal:
            return GeneralIntentDecision(intent="planning")

        if knowledge_signal:
            return GeneralIntentDecision(
                intent="knowledge",
                intents=["knowledge"],
                need_retrieval=_needs_knowledge_retrieval(
                    normalized, parsed, self.retrieval_markers
                ) or (
                    contextual_follow_up
                    and _needs_knowledge_retrieval(
                        _compact(conversation_summary or ""),
                        None,
                        self.retrieval_markers,
                    )
                ),
            )
        if not _is_tourism_message(normalized, parsed) and not _is_casual_message(normalized):
            return GeneralIntentDecision(intent="out_of_scope")
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
                intents=["route"],
                origin=_clean_place(message),
                destination=destination,
                route_mode=_route_mode(_compact(previous_user)),
                missing_fields=[] if destination else ["destination"],
            )
        if "还需要目的地" in assistant:
            origin, _ = _extract_route_places(previous_user)
            return GeneralIntentDecision(
                intent="route",
                intents=["route"],
                origin=origin,
                destination=_clean_place(message),
                route_mode=_route_mode(_compact(previous_user)),
                missing_fields=[] if origin else ["origin"],
            )
        if "还需要查询天气的城市" in assistant:
            forecast_dates = _weather_dates_for_message(
                previous_user,
                self.today_provider(),
            )
            return GeneralIntentDecision(
                intent="weather",
                intents=["weather"],
                city=_clean_place(message),
                target_date=forecast_dates[0],
                forecast_dates=forecast_dates,
            )
        if "想以哪个具体地点为中心" in assistant:
            center = _extract_nearby_center(message) or _clean_nearby_answer(message)
            category = _extract_nearby_category(previous_user) or _extract_nearby_category(message)
            return _nearby_decision(center, category)
        if "告诉我一个具体地点和想找的服务" in assistant:
            current_center = _extract_nearby_center(message)
            current_category = _extract_nearby_category(message)
            center = _extract_nearby_center(previous_user) or current_center
            category = _extract_nearby_category(previous_user) or current_category
            if not center and not current_category:
                center = _clean_nearby_answer(message)
            return _nearby_decision(center, category)
        if "附近想找什么" in assistant:
            current_center = _extract_nearby_center(message)
            current_category = _extract_nearby_category(message)
            center = _extract_nearby_center(previous_user) or current_center
            category = _extract_nearby_category(previous_user) or current_category
            if not center and not current_category:
                center = _clean_nearby_answer(message)
            return _nearby_decision(center, category)
        if "另一个参考地点" in assistant:
            center = _extract_nearby_center(message) or _clean_nearby_answer(message)
            category = _extract_nearby_category(message) or _extract_nearby_category(previous_user)
            return _nearby_decision(center, category)
        origin, destination = _extract_route_places(previous_user)
        if origin and destination and _is_route_clarification(assistant):
            replacement = _clean_route_follow_up_place(message)
            if replacement:
                if _clarified_route_side(assistant, origin, destination) == "destination":
                    destination = replacement
                else:
                    origin = replacement
                return GeneralIntentDecision(
                    intent="route",
                    intents=["route"],
                    origin=origin,
                    destination=destination,
                    route_mode=_route_mode(_compact(message + previous_user)),
                )
        return None

    @staticmethod
    def _looks_like_route(normalized: str) -> bool:
        if "从" in normalized and "到" in normalized:
            return True
        if any(marker in normalized for marker in _ROUTE_MARKERS if marker != "交通"):
            return True
        return "交通" in normalized and "到" in normalized


def _weather_dates_for_message(
    message: str,
    today: date,
    *,
    recent_messages: Sequence[dict[str, str]] = (),
    inherit_previous: bool = False,
) -> list[date]:
    requested = requested_weather_dates(message, today)
    if requested:
        return requested
    resolved = resolve_start_date(message, reference_date=today)
    if resolved is not None:
        return [resolved]
    if inherit_previous:
        for item in reversed(recent_messages):
            if item.get("role") != "user":
                continue
            previous_text = str(item.get("content", ""))
            previous_dates = requested_weather_dates(previous_text, today)
            if previous_dates:
                return previous_dates
            previous_date = resolve_start_date(previous_text, reference_date=today)
            if previous_date is not None:
                return [previous_date]
    return resolve_weather_dates(message, today)


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


def _clean_route_follow_up_place(value: str) -> str:
    """Extract a place name from a clarification such as a bullet answer."""

    result = re.sub(r"^[\s\-—*•·]+", "", value.strip())
    result = re.split(r"[（(]", result, maxsplit=1)[0]
    return result.strip().strip("，。！？,.!?；;：:")


def _is_route_clarification(assistant: str) -> bool:
    return any(
        marker in assistant
        for marker in ("哪一段", "哪一处", "哪一站", "具体是哪", "具体是哪个", "有好几段", "有几段")
    )


def _clarified_route_side(assistant: str, origin: str, destination: str) -> str:
    """Choose which endpoint the assistant asked the user to clarify."""

    origin_markers = (f"{origin}有好几段", f"{origin}有几段", f"{origin}哪一段")
    destination_markers = (
        f"{destination}有好几段",
        f"{destination}有几段",
        f"{destination}哪一段",
    )
    if any(marker in assistant for marker in destination_markers):
        return "destination"
    if any(marker in assistant for marker in origin_markers):
        return "origin"
    origin_position = assistant.find(origin)
    destination_position = assistant.find(destination)
    if destination_position >= 0 and (
        origin_position < 0 or destination_position < origin_position
    ):
        return "destination"
    return "origin"


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
    if any(marker in normalized for marker in ("开车", "驾车", "自驾", "打车", "出租车", "网约车")):
        return "driving"
    return "transit"


def _parsed_city(parsed) -> str | None:
    cities = getattr(parsed, "cities", None) or []
    return cities[0] if cities else None


def _has_destination(parsed) -> bool:
    return bool(getattr(parsed, "province", None) or (getattr(parsed, "cities", None) or []))


def _needs_knowledge_retrieval(
    normalized: str,
    parsed,
    markers: Sequence[str] | None = None,
) -> bool:
    """Check whether the message matches a document-backed travel topic."""

    del parsed
    active_markers = _RETRIEVAL_TOPIC_MARKERS if markers is None else markers
    return any(marker in normalized for marker in active_markers)


def is_contextual_follow_up(message: str) -> bool:
    """Identify short ordinary-chat references that need prior topic context."""

    normalized = _compact(message)
    if (
        not normalized
        or _is_weather_date_follow_up(normalized)
        or any(marker in normalized for marker in _NON_INHERITED_TOOL_FOLLOW_UPS)
    ):
        return False
    return len(normalized) <= 28 and (
        normalized.startswith(("那", *_CONTEXTUAL_FOLLOW_UP_STARTS))
        or any(marker in normalized for marker in ("还有呢", "再详细说说", "有什么区别", "那呢"))
    )


def _summary_has_tourism_context(summary: str) -> bool:
    normalized = _compact(summary)
    return any(marker in normalized for marker in _TRAVEL_SCOPE_MARKERS)


def _recent_history_has_tourism_context(messages: Sequence[dict[str, str]]) -> bool:
    recent_text = _compact(" ".join(
        str(item.get("content") or "")
        for item in messages[-8:]
        if item.get("role") in {"user", "assistant"}
    ))
    return any(marker in recent_text for marker in _TRAVEL_SCOPE_MARKERS)


def _is_weather_date_follow_up(normalized: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:(?:那|那么|再|然后)?(?:今天|明天|后天|大后天|周末|下周[一二三四五六日天]|周[一二三四五六日天]|星期[一二三四五六日天]))(?:呢|天气呢|天气|怎么样|如何|也看看|也查一下)?",
            normalized,
        )
    )


def _is_weather_location_follow_up(normalized: str) -> bool:
    return any(normalized.startswith(prefix) for prefix in ("那边", "那里", "那", "这边", "这里")) and any(
        marker in normalized for marker in _WEATHER_MARKERS
    )


def _is_route_mode_follow_up(normalized: str) -> bool:
    return (
        len(normalized) <= 20
        and normalized.startswith(("那", "改成", "换成", "如果"))
        and any(
            marker in normalized
            for marker in ("步行", "走路", "公交", "地铁", "开车", "驾车", "自驾", "打车")
        )
    )


def _is_nearby_request(message: str, normalized: str) -> bool:
    if not any(marker in normalized for marker in _NEARBY_MARKERS):
        return False
    if not _extract_nearby_category(message) and any(
        marker in normalized for marker in _WEATHER_MARKERS
    ):
        return False
    if not _extract_nearby_category(message) and any(
        marker in normalized for marker in ("好玩", "景点", "值得", "风景", "游玩")
    ):
        return False
    return bool(
        _extract_nearby_category(message)
        or any(marker in normalized for marker in ("找", "查询", "查一下", "搜索", "有没有", "哪里有", "有什么", "推荐"))
    )


def _extract_nearby_category(message: str) -> str | None:
    normalized = _compact(message)
    for category, aliases in _NEARBY_CATEGORY_ALIASES:
        if any(alias in normalized for alias in aliases):
            return category
    return None


def _extract_nearby_center(message: str) -> str | None:
    clean = message.strip()
    prefix = None
    for marker in _NEARBY_MARKERS:
        position = clean.find(marker)
        if position >= 0:
            prefix = clean[:position]
            break
    if prefix is None:
        return None
    prefix = prefix.strip(" ，,。！？!?；;：:")
    prefix = re.sub(
        r"^(?:请问|麻烦|我现在在|我刚到|我到了|我在|现在在|帮我找一下|帮我找|请帮我找|请帮我|帮我|给我|想找|想在|找一下|查一下|搜索一下|查询一下|找|查询|搜索|查|看看|以|在|到)+",
        "",
        prefix,
    )
    prefix = re.sub(r"(?:帮我找|找一下|找个|查询|搜索|看看)$", "", prefix)
    prefix = prefix.strip(" 的这那边里 ，,。！？!?；;：:")
    if not prefix or prefix in {"我", "这里", "那里", "附近", "周边", "周围"}:
        return None
    return prefix[:40]


def _clean_nearby_answer(message: str) -> str | None:
    result = re.sub(r"^[\s\-—*•·]+", "", message.strip())
    result = re.sub(r"^(?:我在|现在在|以|在|到|请问|麻烦|就是|那就|那|去)+", "", result)
    result = re.sub(r"(?:附近|周边|周围|旁边|一带|吧|就行|可以)$", "", result)
    result = result.strip(" 的这那边里 ，,。！？!?；;：:")
    if not result or len(result) > 40:
        return None
    return result


def _nearby_decision(center: str | None, category: str | None) -> GeneralIntentDecision:
    missing = []
    if not center:
        missing.append("nearby_center")
    if not category:
        missing.append("nearby_category")
    return GeneralIntentDecision(
        intent="nearby",
        intents=["nearby"],
        nearby_center=center,
        nearby_category=category,
        missing_fields=missing,
    )


def _weather_context_city(
    recent_messages: Sequence[dict[str, str]],
    conversation_summary: str | None,
) -> str | None:
    if recent_messages and recent_messages[-1].get("role") == "assistant":
        last_reply = str(recent_messages[-1].get("content") or "")
        weather_reply = any(
            marker in last_reply
            for marker in (*_WEATHER_MARKERS, "℃", "°C", "多云", "晴", "小雨", "大雨")
        )
        if weather_reply:
            for item in reversed(recent_messages[:-1]):
                content = str(item.get("content") or "")
                if item.get("role") != "user":
                    continue
                if any(marker in content for marker in _WEATHER_MARKERS):
                    city = _extract_weather_city(content)
                    if city:
                        return city
                break
    if conversation_summary:
        match = re.search(
            r"天气查询城市\s*[:：]\s*([^，。；;、\s]+)",
            conversation_summary,
        )
        if match:
            return _clean_place(match.group(1)) or None
    return None


def _is_weather_lookup_request(message: str, parsed) -> bool:
    if _parsed_city(parsed):
        return True
    candidate = _extract_weather_city(message)
    if candidate and candidate not in {"高温", "低温", "寒冷", "炎热", "冬季", "雨季"}:
        return True
    return any(
        marker in _compact(message)
        for marker in ("今天", "明天", "后天", "预报", "气温", "温度", "下雨", "降雨", "降水")
    )


def _is_tourism_message(normalized: str, parsed) -> bool:
    return _has_destination(parsed) or any(
        marker in normalized for marker in _TRAVEL_SCOPE_MARKERS
    )


def _is_casual_message(normalized: str) -> bool:
    return any(marker in normalized for marker in _CASUAL_MARKERS)


def _compact(value: str) -> str:
    return re.sub(r"[，。！？、,.!?；;：:\s]", "", value).lower()


def _unique_compact(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        marker = _compact(str(value))
        if marker and marker not in seen:
            seen.add(marker)
            result.append(marker)
    return tuple(result)


__all__ = ["GeneralIntentRouter"]
