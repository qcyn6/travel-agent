from datetime import date, timedelta

import pytest

from backend.models.conversation import load_requirement_draft
from backend.models.requirement import TravelRequirement
from backend.requirements.dates import format_date_zh, resolve_start_date
from backend.requirements import RequirementBoundaryError, RequirementParser


@pytest.mark.parametrize(
    ("text", "province", "cities", "days", "scope"),
    [
        ("杭州玩一天，喜欢拍照和历史文化", None, ["杭州"], 1, "single_city"),
        ("杭州玩三天，喜欢自然和人文", None, ["杭州"], 3, "single_city"),
        ("云南玩七天，想去两三个城市，喜欢自然、古城和摄影", "云南省", [], 7, "multi_city"),
        ("云南玩七天，安排同省多城市", "云南省", [], 7, "multi_city"),
        ("杭州和绍兴玩四天", None, ["杭州", "绍兴"], 4, "multi_city"),
    ],
)
def test_parse_acceptance_cases(text, province, cities, days, scope):
    result = RequirementParser().parse(text)

    assert result.province == province
    assert result.cities == cities
    assert result.days == days
    assert result.travel_scope == scope


def test_parse_keeps_free_form_preference():
    result = RequirementParser().parse("我想在杭州玩3天，希望轻松一点，适合拍照")

    assert result.preference_text is not None
    assert "轻松" in result.preference_text
    assert result.interests == ["摄影"]


def test_parse_rewrites_natural_scenery_to_database_interest_tag():
    result = RequirementParser().parse("我想在杭州玩3天，喜欢自然风光")

    assert result.interests == ["自然"]


def test_parser_leaves_route_mode_for_map_recommendation():
    result = RequirementParser().parse("2026年10月3日去杭州玩三天，主要坐地铁")

    assert result.start_date.isoformat() == "2026-10-03"
    assert result.transport_mode == "auto"


def test_parser_auto_selects_multi_city_for_four_to_seven_days():
    result = RequirementParser().parse("云南玩5天，喜欢自然")

    assert result.travel_scope == "multi_city"


def test_arrival_transport_does_not_fill_local_attraction_transport_mode():
    result = RequirementParser().parse("坐飞机去杭州玩三天，喜欢自然")

    assert result.transport_mode == "auto"


def test_parse_rejects_empty_text():
    with pytest.raises(ValueError, match="不能为空"):
        RequirementParser().parse("  ")


@pytest.mark.parametrize(
    ("text", "province", "cities"),
    [
        ("北京玩三天，喜欢历史文化", "北京市", ["北京"]),
        ("四川成都玩三天，喜欢自然", "四川省", ["成都"]),
        ("广西桂林玩三天，喜欢自然和摄影", "广西壮族自治区", ["桂林"]),
        ("新疆乌鲁木齐玩三天，喜欢摄影", "新疆维吾尔自治区", ["乌鲁木齐"]),
        ("海南省玩三天，喜欢海滨", "海南省", []),
    ],
)
def test_parse_nationwide_province_and_city_aliases(text, province, cities):
    result = RequirementParser().parse(text)

    assert result.province == province
    assert result.cities == cities


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("今天出发", date(2026, 9, 15)),
        ("明天出发", date(2026, 9, 16)),
        ("后天出发", date(2026, 9, 17)),
        ("大后天出发", date(2026, 9, 18)),
        ("下周一出发", date(2026, 9, 21)),
        ("下下周三出发", date(2026, 9, 30)),
        ("这个周末出发", date(2026, 9, 19)),
        ("9月20日出发", date(2026, 9, 20)),
    ],
)
def test_resolves_relative_dates(phrase, expected):
    assert resolve_start_date(phrase, reference_date=date(2026, 9, 15)) == expected


def test_parser_normalizes_relative_date_for_downstream_planning():
    result = RequirementParser().parse(
        "明天去杭州玩三天，喜欢自然",
        reference_date=date(2026, 9, 15),
    )

    assert result.start_date == date(2026, 9, 16)
    assert format_date_zh(result.start_date) == "2026年9月16日（周三）"


def test_parser_extracts_daily_play_window_from_chinese_time_range():
    result = RequirementParser().parse("明天去杭州玩三天，喜欢自然，每天早上8点到晚上9点")

    assert result.daily_start_time == "08:00"
    assert result.daily_end_time == "21:00"


def test_parser_supports_partial_daily_time_updates():
    result = RequirementParser().parse("每天8点开始")

    assert result.daily_start_time == "08:00"
    assert result.daily_end_time is None


def test_requirement_rejects_cross_day_daily_time_window():
    with pytest.raises(ValueError, match="不支持跨日"):
        TravelRequirement(daily_start_time="20:00", daily_end_time="08:00")


@pytest.mark.parametrize("text", ["杭州玩31天", "杭州玩365天", "杭州玩三十一天", "杭州玩三百六十五天"])
def test_parser_rejects_trip_lengths_outside_product_boundary(text):
    with pytest.raises(RequirementBoundaryError, match="1～7天"):
        RequirementParser().parse(text)


def test_parser_does_not_treat_calendar_day_as_trip_length():
    result = RequirementParser().parse("2026年10月3日去杭州")

    assert result.days is None
    assert result.start_date == date(2026, 10, 3)


def test_parser_finds_trip_length_after_calendar_date():
    result = RequirementParser().parse("2026年10月3日去杭州玩3天")

    assert result.days == 3


def test_parser_rejects_departure_date_before_reference_date():
    with pytest.raises(RequirementBoundaryError, match="明天"):
        RequirementParser().parse(
            "2026年9月14日去杭州玩3天，喜欢自然",
            reference_date=date(2026, 9, 15),
        )


def test_parser_rejects_departure_date_on_reference_date():
    with pytest.raises(RequirementBoundaryError, match="明天"):
        RequirementParser().parse(
            "2026年9月15日去杭州玩3天，喜欢自然",
            reference_date=date(2026, 9, 15),
        )


def test_requirement_contract_rejects_out_of_range_days():
    with pytest.raises(ValueError, match="1～7天"):
        TravelRequirement(days=365)


def test_legacy_conversation_draft_is_reopened_as_incomplete():
    draft = load_requirement_draft(
        {"province": "浙江省", "cities": [], "days": 365, "interests": ["自然"]}
    )

    assert draft.days is None
    assert draft.is_ready() is False


def test_legacy_single_city_draft_over_three_days_is_reopened_as_incomplete():
    draft = load_requirement_draft(
        {
            "cities": ["大理市"],
            "days": 7,
            "interests": ["自然"],
            "travel_scope": "single_city",
        }
    )

    assert draft.days is None
    assert draft.travel_scope == "auto"
    assert draft.is_ready() is False


def test_requirement_patch_rejects_past_departure_date():
    from backend.models.conversation import RequirementDraftPatch

    with pytest.raises(ValueError, match="明天"):
        RequirementDraftPatch(start_date=date.today() - timedelta(days=1))


def test_parser_marks_explicit_province_recommendation():
    draft = RequirementParser().parse("你可以推荐一个省份吗")

    assert draft.province is None
    assert draft.province_preference_confirmed is True
    assert draft.city_preference_confirmed is False
