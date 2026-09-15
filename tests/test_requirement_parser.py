from datetime import date, timedelta

import pytest

from backend.requirements.dates import format_date_zh, resolve_start_date
from backend.requirements import RequirementParser


@pytest.mark.parametrize(
    ("text", "province", "cities", "days", "scope"),
    [
        ("杭州玩一天，喜欢拍照和历史文化", None, ["杭州"], 1, "single_city"),
        ("杭州玩三天，喜欢自然和人文", None, ["杭州"], 3, "single_city"),
        ("云南玩七天，想去两三个城市，喜欢自然、古城和摄影", "云南省", [], 7, "multi_city"),
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


def test_parse_extracts_start_date_and_transport_mode():
    result = RequirementParser().parse("2026年10月3日去杭州玩三天，主要坐地铁")

    assert result.start_date.isoformat() == "2026-10-03"
    assert result.transport_mode == "transit"


def test_parse_rejects_empty_text():
    with pytest.raises(ValueError, match="不能为空"):
        RequirementParser().parse("  ")


@pytest.mark.parametrize(
    ("text", "province", "cities"),
    [
        ("北京玩三天，喜欢历史文化", "北京市", ["北京"]),
        ("四川成都玩三天，喜欢自然", "四川省", ["成都"]),
        ("广西桂林玩五天，喜欢自然和摄影", "广西壮族自治区", ["桂林"]),
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
