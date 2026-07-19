DEFAULT_AI_NAME = "AI"
DEFAULT_USER_NAME = "User"
DEFAULT_USER_DISPLAY_NAME = "用户"
DEFAULT_USER_ALIASES = ["对方"]
HUMAN_NAME_MAX_LENGTH = 20

GENERIC_AI_NAME = "AI"
GENERIC_USER_NAME = "User"
GENERIC_USER_DISPLAY_NAME = "用户"
GENERIC_USER_ALIASES = ["对方"]


def _clean_string(value, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _clean_list(value, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return [item for item in items if item] or list(default)


def identity_names(config: dict | None = None) -> dict:
    cfg = {}
    if isinstance(config, dict) and isinstance(config.get("identity"), dict):
        cfg = config["identity"]

    aliases = _clean_list(cfg.get("user_aliases"), DEFAULT_USER_ALIASES)
    ai_name = _clean_string(cfg.get("ai_name"), DEFAULT_AI_NAME)
    user_name = _clean_string(cfg.get("user_name"), DEFAULT_USER_NAME)
    user_display_name = _clean_string(
        cfg.get("user_display_name") or cfg.get("human_name"),
        DEFAULT_USER_DISPLAY_NAME,
    )
    relationship_terms = list(dict.fromkeys([ai_name, user_name, user_display_name, *aliases]))
    return {
        "ai_name": ai_name,
        "user_name": user_name,
        "user_display_name": user_display_name,
        "user_aliases": aliases,
        "user_aliases_text": "、".join(aliases),
        "relationship_terms": relationship_terms,
    }


def identity_human_name(
    config: dict | None = None,
    default: str = DEFAULT_USER_DISPLAY_NAME,
) -> str:
    """Resolve the human-facing name from identity fields only."""
    identity_cfg = config.get("identity") if isinstance(config, dict) else None
    if isinstance(identity_cfg, dict):
        for key in ("user_display_name", "human_name", "user_name"):
            value = str(identity_cfg.get(key) or "").strip()
            if value:
                return value
    return default


def effective_human_name(
    config: dict | None = None,
    default: str = DEFAULT_USER_DISPLAY_NAME,
) -> str:
    """Resolve an explicit human override, then fall back to identity."""
    if isinstance(config, dict):
        explicit = str(config.get("human") or "").strip()
        if explicit:
            return explicit
    return identity_human_name(config, default=default)


def effective_ai_name(
    config: dict | None = None,
    default: str = DEFAULT_AI_NAME,
) -> str:
    """Resolve the configured AI name without exposing unrelated identity data."""
    identity_cfg = config.get("identity") if isinstance(config, dict) else None
    if isinstance(identity_cfg, dict):
        value = str(identity_cfg.get("ai_name") or "").strip()
        if value:
            return value
    return default


def validate_human_name(value: object, *, allow_empty: bool = False) -> str:
    """Validate a dashboard-safe human display name and return it trimmed."""
    if not isinstance(value, str):
        raise ValueError("human name must be a string")
    text = value.strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError("human name must not be empty")
    if len(text) > HUMAN_NAME_MAX_LENGTH:
        raise ValueError(
            f"human name must be ≤ {HUMAN_NAME_MAX_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("human name must not contain control characters")
    return text


def generic_identity_names() -> dict:
    return identity_names(
        {
            "identity": {
                "ai_name": GENERIC_AI_NAME,
                "user_name": GENERIC_USER_NAME,
                "user_display_name": GENERIC_USER_DISPLAY_NAME,
                "user_aliases": GENERIC_USER_ALIASES,
            }
        }
    )


def render_identity_template(template: str, names: dict) -> str:
    text = template
    for key, value in names.items():
        if isinstance(value, list):
            continue
        text = text.replace("{" + key + "}", str(value))
    return text
