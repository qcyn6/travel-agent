from backend.tag_matching import compact_tag_match_score
from backend.tag_vocabulary import (
    canonicalize_interest,
    canonicalize_tag,
    compact_tag_keys,
    interest_tag_keys,
    is_canonical_tag,
)


def test_compressed_yunnan_tags_are_canonical() -> None:
    assert is_canonical_tag("滨水")
    assert is_canonical_tag("生态")


def test_new_broad_tag_aliases_are_normalized() -> None:
    assert canonicalize_tag("滨水景观") == "滨水"
    assert canonicalize_tag("生态旅游") == "生态"


def test_compressed_and_detailed_tags_share_matching_meanings() -> None:
    assert compact_tag_keys("古城") == frozenset({"古镇"})
    assert compact_tag_match_score(["古镇"], ["古城"]) == 1
    assert compact_tag_match_score(["滨水"], ["湖泊"]) == 1
    assert compact_tag_match_score(["生态"], ["森林"]) == 1
    assert compact_tag_match_score(["建筑"], ["古建"]) == 1
    assert compact_tag_match_score(["自然"], ["历史文化"]) == 0


def test_six_public_interests_expand_to_requested_attraction_tags() -> None:
    assert canonicalize_interest("古镇") == "古城"
    assert canonicalize_interest("人文") == "历史文化"
    assert "古镇" in interest_tag_keys("古城")
    assert compact_tag_match_score(["人文"], ["古城"]) == 1
    assert compact_tag_match_score(["地质景观"], ["自然"]) == 1
    assert compact_tag_match_score(["建筑"], ["摄影"]) == 1
    assert compact_tag_match_score(["温泉"], ["休闲"]) == 1
    assert compact_tag_match_score(["夜游"], ["美食"]) == 1
