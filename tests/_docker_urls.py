"""URL helpers shared by Docker integration tests."""

from urllib.parse import urlsplit


def extra_mcp_url(mcp_url: str) -> str:
    """Replace the trailing main MCP path while preserving any proxy prefix."""
    parsed = urlsplit(mcp_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/mcp"):
        raise ValueError("Docker MCP integration URL must end with /mcp")
    extra_path = f"{path[:-len('/mcp')]}/mcp-extra"
    return parsed._replace(path=extra_path, query="", fragment="").geturl()
