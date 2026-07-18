import math
from datetime import datetime, timezone

import pytest

from runtime_values import (
    age_hours_since,
    bool_value,
    clamp_valence_arousal,
    clamp_float,
    enabled_value,
    finite_float,
    finite_float_between,
    finite_unit_float,
    float_between,
    float_value,
    int_between,
    int_value,
    iso_date_key,
    lower_text,
    metadata_dict,
    metadata_view,
    numeric_int_between,
    optional_float,
    optional_int,
    parse_comparable_datetime,
    parse_utc_datetime,
    rounded_unit_float,
    text_value,
    unit_float,
    utc_now,
    utc_now_iso,
    valid_memory_id,
)


def test_shared_scalar_and_clock_values_preserve_runtime_contracts() -> None:
    assert text_value("  Amy  ") == "Amy"
    assert lower_text("  AMY  ") == "amy"
    assert int_value("12", 0) == 12
    assert int_value("bad", 7) == 7
    assert int_value("bad") == 0
    assert optional_int("12") == 12
    assert optional_int("") is None
    assert optional_int("bad") is None
    assert optional_float("1.5") == 1.5
    assert optional_float("bad") is None
    assert float_value("bad", 2.5) == 2.5
    assert utc_now().tzinfo is timezone.utc
    assert utc_now_iso().endswith("+00:00")
    assert age_hours_since(
        "2026-07-17T10:00:00Z",
        datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    ) == 2.0


def test_boolean_parsers_keep_explicit_and_enable_flag_semantics_distinct() -> None:
    assert bool_value("unexpected") is True
    assert enabled_value("unexpected") is False
    assert enabled_value(None, True) is True
    assert bool_value("off", True) is False


def test_integer_parsers_preserve_decimal_config_support_only_where_requested() -> None:
    assert int_between("3.7", 9, 1, 10) == 9
    assert numeric_int_between("3.7", 9, 1, 10) == 3
    assert int_between(math.inf, 9, 1, 10) == 9


def test_float_parsers_keep_finite_default_policy_explicit() -> None:
    assert float_between(math.inf, 0.4, 0.0, 1.0) == 1.0
    assert finite_float_between(math.inf, 0.4, 0.0, 1.0) == 0.4
    assert finite_float(math.nan, 0.3) == 0.3
    assert clamp_float("bad", 0.2, 0.8) == 0.2
    assert unit_float(2.0, 0.4) == 1.0
    assert finite_unit_float(math.inf, 0.4) == 0.4
    assert rounded_unit_float(0.1239) == 0.124


def test_valence_arousal_pair_falls_back_atomically() -> None:
    assert clamp_valence_arousal({"valence": 2, "arousal": -1}) == (1.0, 0.0)
    assert clamp_valence_arousal({"valence": 0.8, "arousal": "bad"}) == (0.5, 0.3)


@pytest.mark.parametrize("value", ["bucket-1", "moment:2", "profile.fact_3"])
def test_valid_memory_id_accepts_supported_identifiers(value) -> None:
    assert valid_memory_id(value)


def test_metadata_and_date_helpers_preserve_boundary_behavior() -> None:
    source = {"metadata": {"importance": 8}}
    extracted = metadata_dict(source)
    extracted["importance"] = 1

    assert source["metadata"]["importance"] == 8
    assert metadata_view(source) is source["metadata"]
    assert iso_date_key("created 2026-07-17T12:00:00Z") == "2026-07-17"
    assert iso_date_key("unknown") == ""
    assert parse_utc_datetime("2026-07-17T12:00:00Z") == datetime(
        2026, 7, 17, 12, tzinfo=timezone.utc
    )
    assert parse_utc_datetime("not-a-date") is None
    assert parse_comparable_datetime("2026-07-17T12:00:00Z", timezone.utc) == datetime(
        2026, 7, 17, 12, tzinfo=timezone.utc
    )
    assert parse_comparable_datetime("2026-07-17T12:00:00Z", None) == datetime(
        2026, 7, 17, 12
    )
