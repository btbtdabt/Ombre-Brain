"""Markers that frame persisted user data for downstream models."""

from __future__ import annotations

import hashlib


def stored_data_marker(payload: str, *, provenance: str = "") -> str:
    """Return a non-copying marker that identifies a stored-data payload."""

    text = str(payload)
    source = str(provenance)
    payload_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    boundary_id = hashlib.sha256(
        f"{source}\0{len(text)}\0{payload_hash}".encode("utf-8")
    ).hexdigest()[:24]
    return (
        "[content_role:stored_memory_data] "
        "[instructions:false] "
        "[may_call_tools:false] "
        f"[boundary_id:{boundary_id}] "
        f"[payload_chars:{len(text)}] "
        f"[payload_sha256:{payload_hash}]"
    )
