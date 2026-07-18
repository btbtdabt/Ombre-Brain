"""Shared coercion helpers for active runtime configuration and API boundaries."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
from typing import Any


MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def coerce_id(value: Any) -> str:
    return "" if value is None else str(value).strip()


def text_value(value: Any) -> str:
    return str(value or "").strip()


def lower_text(value: Any) -> str:
    return text_value(value).lower()


def valid_memory_id(value: Any) -> bool:
    return bool(MEMORY_ID_RE.fullmatch(coerce_id(value)))


def bool_value(value: Any, default: bool = False) -> bool:
    """Parse common explicit booleans and preserve truthiness for other values."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return bool(value)


def enabled_value(value: Any, default: bool = False) -> bool:
    """Treat strings as enable flags: only explicit true spellings enable them."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return bool(value)


def int_between(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def numeric_int_between(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Clamp an integer while retaining support for decimal numeric strings."""

    try:
        parsed = int(float(value))
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def float_between(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def float_value(value: Any, default: float = 0.0) -> float:
    parsed = optional_float(value)
    return default if parsed is None else parsed


def finite_float_between(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def clamp_float(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return float_between(value, minimum, minimum, maximum)


def unit_float(value: Any, default: float) -> float:
    return float_between(value, default, 0.0, 1.0)


def finite_unit_float(value: Any, default: float) -> float:
    return finite_float_between(value, default, 0.0, 1.0)


def rounded_unit_float(value: Any, default: float = 0.5, digits: int = 3) -> float:
    return round(unit_float(value, default), digits)


def clamp_valence_arousal(
    metadata: Mapping[str, Any],
    default_valence: float = 0.5,
    default_arousal: float = 0.3,
) -> tuple[float, float]:
    """Clamp a valence/arousal pair, falling back atomically if either is invalid."""

    try:
        valence = max(0.0, min(1.0, float(metadata.get("valence", default_valence))))
        arousal = max(0.0, min(1.0, float(metadata.get("arousal", default_arousal))))
        return valence, arousal
    except (TypeError, ValueError):
        return default_valence, default_arousal


def finite_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def metadata_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metadata = value.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def metadata_view(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def iso_date_key(value: Any) -> str:
    match = _ISO_DATE_RE.search(str(value or ""))
    return match.group(0) if match else ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def age_hours_since(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_utc_datetime(value)
    if parsed is None:
        return None
    reference = now or utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)
    return max(0.0, (reference - parsed).total_seconds() / 3600.0)


def parse_utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_comparable_datetime(value: Any, compare_timezone: tzinfo | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if compare_timezone is None:
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=compare_timezone)
    return parsed.astimezone(compare_timezone)
