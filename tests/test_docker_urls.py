import pytest

from tests._docker_urls import extra_mcp_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://ombre-brain:8000/mcp", "http://ombre-brain:8000/mcp-extra"),
        (
            "https://example.test/ombre/mcp?debug=1#fragment",
            "https://example.test/ombre/mcp-extra",
        ),
        ("https://example.test/prefix/mcp/", "https://example.test/prefix/mcp-extra"),
    ],
)
def test_extra_mcp_url_preserves_proxy_path_prefix(url, expected):
    assert extra_mcp_url(url) == expected


def test_extra_mcp_url_rejects_non_mcp_endpoint():
    with pytest.raises(ValueError, match="end with /mcp"):
        extra_mcp_url("https://example.test/prefix")
