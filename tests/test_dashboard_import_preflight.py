from pathlib import Path


def test_dashboard_import_flow_contains_preflight_confirmation():
    for rel in ("frontend/dashboard.html",):
        html = Path(rel).read_text(encoding="utf-8")

        assert 'id="import-preflight-panel"' in html
        assert 'id="import-start-confirm-btn"' in html
        assert "async function runImportPreflight(file)" in html
        assert "function renderImportPreflight" in html
        assert "/api/import/preflight" in html
        assert 'id="import-mode"' in html
        assert '<option value="auto"' in html
        assert '<option value="operit"' in html
        assert '<option value="conversation"' in html
        assert 'id="import-operit-tagging"' in html
        assert 'id="import-tagging-progress"' in html
        assert 'id="import-tagging-succeeded"' in html
        assert "operit_tagging" in html
        assert "import_mode" in html
        assert "importPreflightRunning" in html
        assert "importPreflightQueued" in html

