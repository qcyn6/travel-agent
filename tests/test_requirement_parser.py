import pytest

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
