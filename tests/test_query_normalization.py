from memory_layers import LAYER_SOURCE_RECORD, is_source_record_bucket
from query_normalization import (
    compact_lookup_key,
    compact_phrase_key,
    compact_symbol_key,
    unique_text_values,
)


def test_query_key_normalizers_preserve_their_distinct_contracts() -> None:
    value = "  Amy_Project:V1 / 星 河！ "

    assert compact_lookup_key(value) == "amyprojectv1星河"
    assert compact_symbol_key(value) == "amy_project:v1星河"
    assert compact_phrase_key(value) == "amy_projectv1/星河"


def test_unique_text_values_preserves_first_occurrence() -> None:
    assert unique_text_values([" Amy ", "", "Amy", None, "秋"]) == ["Amy", "秋"]


def test_source_record_detection_uses_the_canonical_layer_inference() -> None:
    bucket = {"metadata": {"type": "source"}}

    assert is_source_record_bucket(bucket)
    assert is_source_record_bucket(None) is False
    assert LAYER_SOURCE_RECORD == "source_record"
