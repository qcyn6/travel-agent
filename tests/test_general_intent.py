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


def test_route_follow_up_understands_an_llm_clarification_for_an_ambiguous_segment():
    decision = _router().classify(
        "- 八达岭长城（最靠东，京藏高速方向）",
        recent_messages=[
            {"role": "user", "content": "长城到天安门怎么走"},
            {
                "role": "assistant",
                "content": "长城有好几段，去天安门的路线差别很大。你指的是哪一段，以及想坐公交/地铁还是自驾？",
            },
        ],
    )

    assert decision.intent == "route"
    assert decision.origin == "八达岭长城"
    assert decision.destination == "天安门"
    assert decision.route_mode == "transit"
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


def test_weather_suitability_question_does_not_become_knowledge_multi_intent():
    decision = _router().classify(
        "杭州明天天气适合出门吗",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
    )

    assert decision.intent == "weather"
    assert decision.intents == ["weather"]
    assert decision.need_retrieval is False


def test_planning_and_knowledge_intents_remain_separate():
    planning = _router().classify("帮我安排杭州三日游", planning_signal=True)
    knowledge = _router().classify(
        "杭州有哪些自然景点",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
        planning_signal=False,
    )

    assert planning.intent == "planning"
    assert knowledge.intent == "knowledge"


def test_generic_knowledge_question_does_not_enter_project_rag():
    decision = _router().classify("介绍一下旅行安全", planning_signal=False)

    assert decision.intent == "knowledge"
    assert decision.need_retrieval is False


def test_future_travel_topic_enters_rag_but_destination_basics_do_not():
    guide = _router().classify("高原旅行注意事项")
    destination = _router().classify(
        "介绍一下云南的历史文化",
        parsed=SimpleNamespace(cities=[], province="云南省"),
    )

    assert guide.intent == "knowledge"
    assert guide.need_retrieval is True
    assert destination.intent == "knowledge"
    assert destination.need_retrieval is False


def test_extended_travel_topics_enter_rag():
    for message in (
        "徒步登山需要注意什么",
        "海边旅行要注意什么",
        "冬季旅行怎么准备",
        "高温出游要注意什么",
        "出发前需要准备哪些证件",
    ):
        decision = _router().classify(message)
        assert decision.intent == "knowledge"
        assert decision.need_retrieval is True


def test_generic_high_temperature_advice_does_not_require_a_weather_city():
    decision = _router().classify("高温天气出游要注意什么")

    assert decision.intent == "knowledge"
    assert decision.need_retrieval is True


def test_retrieval_gate_can_use_keywords_from_knowledge_documents():
    router = GeneralIntentRouter(
        today_provider=lambda: date(2026, 9, 20),
        retrieval_markers=("炎热",),
    )

    decision = router.classify("炎热地区出游要注意什么")

    assert decision.intent == "knowledge"
    assert decision.need_retrieval is True


def test_public_transport_knowledge_question_is_not_a_route_query():
    decision = _router().classify(
        "杭州有哪些公共交通",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
    )

    assert decision.intent == "knowledge"


def test_knowledge_and_weather_question_keeps_both_intents():
    decision = _router().classify(
        "杭州有哪些适合拍照的地方，明天天气怎么样",
        parsed=SimpleNamespace(cities=["杭州市"], province=None),
    )

    assert decision.intent == "knowledge"
    assert decision.intents == ["knowledge", "weather"]
    assert decision.includes("weather")
    assert decision.need_retrieval is False
    assert decision.city == "杭州市"
