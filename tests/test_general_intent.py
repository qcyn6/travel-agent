from datetime import date
from types import SimpleNamespace

from backend.conversation.general_intent import GeneralIntentRouter


def _router():
    return GeneralIntentRouter(today_provider=lambda: date(2026, 9, 20))


def test_route_intent_extracts_two_places_and_mode():
    decision = _router().classify("从杭州东站到西湖怎么走，想坐地铁")

    assert decision.intent == "route"
    assert decision.origin == "杭州东站"
    assert decision.destination == "西湖"
    assert decision.route_mode == "transit"
    assert decision.missing_fields == []


def test_route_intent_requests_only_missing_origin():
    decision = _router().classify("西湖怎么去")

    assert decision.intent == "route"
    assert decision.destination == "西湖"
    assert decision.missing_fields == ["origin"]


def test_route_follow_up_uses_previous_destination():
    decision = _router().classify(
        "杭州东站",
        recent_messages=[
            {"role": "user", "content": "西湖怎么去"},
            {"role": "assistant", "content": "要查询这条路线，我还需要出发地点。请告诉我你从哪里出发。"},
        ],
    )

    assert decision.intent == "route"
    assert decision.origin == "杭州东站"
    assert decision.destination == "西湖"
    assert decision.missing_fields == []


def test_weather_intent_uses_parsed_city_and_relative_date():
    parsed = SimpleNamespace(cities=["杭州市"], province=None)
    decision = _router().classify("杭州明天天气怎么样", parsed=parsed)

    assert decision.intent == "weather"
    assert decision.city == "杭州市"
    assert decision.target_date == date(2026, 9, 21)
    assert decision.missing_fields == []


def test_weather_intent_requests_city_when_missing():
    decision = _router().classify("明天天气怎么样")

    assert decision.intent == "weather"
    assert decision.target_date == date(2026, 9, 21)
    assert decision.missing_fields == ["city"]


def test_planning_and_knowledge_intents_remain_separate():
    planning = _router().classify("帮我安排杭州三日游", planning_signal=True)
    knowledge = _router().classify(
        "杭州有哪些自然景点",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
        planning_signal=False,
    )

    assert planning.intent == "planning"
    assert knowledge.intent == "knowledge"


def test_public_transport_knowledge_question_is_not_a_route_query():
    decision = _router().classify(
        "杭州有哪些公共交通",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
    )

    assert decision.intent == "knowledge"
