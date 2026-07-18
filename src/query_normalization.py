"""Canonical text keys used by active recall and Gateway matching."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_LOOKUP_KEY_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_SYMBOL_KEY_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff_.:-]+")
_PHRASE_SEPARATOR_RE = re.compile(
    r"[\s，。！？、,.!?:：;；~～♡❤♥（）()\[\]【】「」『』“”\"'`-]+"
)


def compact_lookup_key(value: Any) -> str:
    """Normalize free text to an alphanumeric/CJK lookup key."""

    return _LOOKUP_KEY_RE.sub("", str(value or "").strip().lower())


def compact_symbol_key(value: Any) -> str:
    """Normalize a term while retaining identifier punctuation."""

    return _SYMBOL_KEY_RE.sub("", str(value or "").strip().lower())


def compact_phrase_key(value: Any) -> str:
    """Remove phrase separators while retaining other meaningful symbols."""

    return _PHRASE_SEPARATOR_RE.sub("", str(value or "").strip().lower())


def unique_text_values(values: Iterable[Any] | None) -> list[str]:
    """Normalize and de-duplicate text values while preserving input order."""

    output: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
