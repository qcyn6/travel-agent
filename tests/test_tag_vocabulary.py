from backend.tag_vocabulary import canonicalize_tag, is_canonical_tag


def test_compressed_yunnan_tags_are_canonical() -> None:
    assert is_canonical_tag("滨水")
    assert is_canonical_tag("生态")


def test_new_broad_tag_aliases_are_normalized() -> None:
    assert canonicalize_tag("滨水景观") == "滨水"
    assert canonicalize_tag("生态旅游") == "生态"
