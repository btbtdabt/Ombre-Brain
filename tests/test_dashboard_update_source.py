from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_version_check_uses_github_api_before_raw_cdn_fallback():
    api_url = "https://api.github.com/repos/P0luz/Ombre-Brain/contents/VERSION?ref=main"
    raw_url = "https://raw.githubusercontent.com/P0luz/Ombre-Brain/main/VERSION?t="

    for rel_path in ("frontend/dashboard.html",):
        html = (ROOT / rel_path).read_text(encoding="utf-8")

        assert api_url in html
        assert raw_url in html
        assert html.index(api_url) < html.index(raw_url)


def test_dashboard_hot_update_surfaces_csrf_proxy_guidance():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    block = html[html.index("window.doHotUpdate = async function()") :]
    block = block[: block.index("window.checkGitHubVersion = async function()")]

    assert "authFetch(BASE + '/api/do-update'" in block
    assert "await fetch(BASE + '/api/do-update'" not in block
    assert "authFetch 只重试 GET/HEAD" in block
    assert "热更新 POST 不会因瞬时网关错误被重放" in block
    assert "failure.error === 'Cross-origin request rejected'" in block
    assert "这不是 CORS 缺失" in block
    assert "OMBRE_TRUSTED_PROXY_CIDRS" in block


def test_dashboard_exposes_faq_in_unified_about_view():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    router = (ROOT / "frontend" / "dashboard-assets" / "core" / "router.js").read_text(
        encoding="utf-8"
    )

    faq_url = "https://docs.qq.com/doc/DRHp6UW9oYmd3QW5Z"
    assert 'data-tab="about"' in html
    assert '<span class="tab-en">About</span>' in html
    assert html.count('id="faq-section"') == 1
    assert faq_url in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'id="faq-view"' not in html
    assert 'faq: "system-about"' in router
    assert 'fromTab.params.section = "faq-section"' in router
