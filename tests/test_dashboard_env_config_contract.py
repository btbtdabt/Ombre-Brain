from pathlib import Path

from web import config_api


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "dashboard.html"
MODELS_DATA = ROOT / "frontend" / "dashboard-assets" / "models-data.js"


def _quick_save_source() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("async function _saveEnvKeys(")
    end = html.index("async function saveCompressKey()", start)
    return html[start:end]


def test_quick_env_save_requires_http_and_payload_success():
    source = _quick_save_source()

    assert "var responseFailed = !r.ok || !d || !d.ok" in source
    assert "if (responseFailed && savedKeys.length === 0)" in source
    assert "HTTP ' + r.status" in source
    assert "保存失败 / Save failed" in source


def test_quick_env_save_confirms_every_requested_field_before_green_success():
    source = _quick_save_source()

    requested = "Object.keys(updates || {})"
    missing = "updatedKeys.indexOf(key) === -1"
    safe_success = "if (!responseFailed && !responsePartial"
    positive_feedback = "color:var(--positive,#7EAD68)"

    assert requested in source
    assert "Array.isArray(d.updated)" in source
    assert missing in source
    assert safe_success in source
    assert source.index(safe_success) < source.index(positive_feedback)


def test_quick_env_save_surfaces_warnings_as_partial_or_failed():
    source = _quick_save_source()

    assert "Array.isArray(d.warnings)" in source
    assert "部分保存 / Partially saved" in source
    assert "color:var(--warning,#B89762)" in source
    assert "警告 / Warning:" in source
    assert "savedKeys.length > 0" in source
    assert "服务器未确认任何请求字段" in source


def test_quick_env_save_honors_partial_and_persistence_contract():
    source = _quick_save_source()

    assert "var responsePartial = !!(d && d.partial)" in source
    assert "Array.isArray(d.persisted)" in source
    assert "unpersistedKeys.length === 0" in source
    assert "未持久化 / Not persisted:" in source
    assert "if (savedKeys.length > 0)" in source
    assert "refreshEnvConfig();" in source


def test_main_env_save_flow_is_left_intact():
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("async function saveEnvConfig()")
    end = html.index("async function _saveEnvKeys(", start)
    source = html[start:end]

    assert "var r = await authFetch('/api/env-config'" in source
    assert "refreshEnvConfig();" in source
    assert "已保存：" in source


def test_models_data_mounts_one_canonical_embedding_editor():
    html = DASHBOARD.read_text(encoding="utf-8")
    source = MODELS_DATA.read_text(encoding="utf-8")

    assert html.count('data-canonical-editor-resource="embedding-settings"') == 1
    assert html.count('data-canonical-editor-panel="models-embeddings"') == 1
    assert 'data-canonical-editor-mounted="false" hidden' in html
    assert (
        "'[data-canonical-editor-resource=\"embedding-settings\"]"
        "[data-canonical-editor-panel=\"models-embeddings\"]'"
    ) in source
    assert "host.appendChild(editor);" in source
    assert source.count("id: 'models-embeddings'") == 1
    assert "mount: mountEmbeddings" in source
    assert "activate: activateEmbeddings" in source


def test_models_data_embedding_contract_keeps_provider_tuple_and_secret_write_only():
    source = MODELS_DATA.read_text(encoding="utf-8")

    for population_contract in (
        "setValue('cfg-emb-enabled', embedding.enabled ? 'true' : 'false')",
        "setValue('cfg-emb-model', embedding.model || '')",
        "setValue('cfg-emb-base-url', embedding.base_url || '')",
        "setValue('cfg-emb-format', embedding.api_format || 'openai_compat')",
        "setValue('cfg-emb-backend', embedding.backend || 'api')",
    ):
        assert population_contract in source

    assert "return getJson('/api/config'" in source
    assert "key.value = '';" in source
    assert "embedding.api_key_masked" in source
    assert config_api._LEGACY_SECTION_FIELDS["embedding"] >= {
        "enabled",
        "model",
        "base_url",
        "api_key",
        "api_format",
        "backend",
    }
    assert (
        config_api._CURRENT_SECRET_ENV_FIELDS[("embedding", "api_key")]
        == "OMBRE_EMBED_API_KEY"
    )
    assert "api_key" in config_api._GATEWAY_ENV_ONLY_FIELDS["embedding"]


def test_models_data_embedding_migration_uses_the_canonical_provider_controls():
    html = DASHBOARD.read_text(encoding="utf-8")

    assert 'id="emb-migrate-btn"' in html
    assert "target_backend: targetBackend" in html
    assert "api_format: (document.getElementById('cfg-emb-format')" in html
    assert "base_url: (document.getElementById('cfg-emb-base-url')" in html
    assert "model: (document.getElementById('cfg-emb-model')" in html
    assert "authFetch(BASE + '/api/embedding/migrate'" in html
    assert "fetch(BASE + '/api/embedding/migrate'" not in html
