from collections.abc import Mapping

from runtime_values import text_value as _tag_key


SELF_ANCHOR_TAG = "自我"
SELF_ANCHOR_ALIASES = {"self_anchor", "first_person_anchor", "first-person-anchor"}
SELF_ANCHOR_KIND_KEYS = {SELF_ANCHOR_TAG, *SELF_ANCHOR_ALIASES}


def _tag_match(value: object) -> bool:
    text = _tag_key(value)
    return text == SELF_ANCHOR_TAG or text.lower() in SELF_ANCHOR_ALIASES


def _metadata_items(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def is_self_anchor_metadata(meta: Mapping[str, object] | None) -> bool:
    if not isinstance(meta, Mapping):
        return False
    if bool(meta.get("self_anchor")):
        return True
    tags = _metadata_items(meta.get("tags")) + _metadata_items(meta.get("bucket_tags"))
    if any(_tag_match(tag) for tag in tags):
        return True
    domains = meta.get("domain", [])
    if isinstance(domains, str):
        domains = [item.strip() for item in domains.split(",")]
    if not isinstance(domains, (list, tuple, set)):
        domains = [domains]
    if any(_tag_match(domain) for domain in domains):
        return True
    for key in ("profile_kind", "bucket_profile_kind", "anchor_kind", "kind", "source"):
        value = _tag_key(meta.get(key))
        if value == SELF_ANCHOR_TAG or value.lower() in SELF_ANCHOR_KIND_KEYS:
            return True
    return False


def is_self_anchor_bucket(bucket: Mapping[str, object] | None) -> bool:
    if not isinstance(bucket, Mapping):
        return False
    metadata = bucket.get("metadata")
    return is_self_anchor_metadata(metadata if isinstance(metadata, Mapping) else {})
