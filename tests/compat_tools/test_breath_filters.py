from tools.breath._filters import bucket_has_tags


def test_bucket_tag_filter_uses_and_semantics_and_accepts_empty_filter() -> None:
    metadata = {"tags": ["preference", "relationship"]}

    assert bucket_has_tags(metadata, []) is True
    assert bucket_has_tags(metadata, ["preference"]) is True
    assert bucket_has_tags(metadata, ["preference", "relationship"]) is True
    assert bucket_has_tags(metadata, ["preference", "missing"]) is False
