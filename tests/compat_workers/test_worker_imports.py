import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "backup_manager",
        "config_diagnostics",
        "darkroom",
        "dream_engine",
        "letter_service",
        "persona_engine",
        "persona_event_selection",
        "portrait_engine",
        "raw_events",
        "reflection_engine",
        "reminder_store",
    ],
)
def test_worker_module_imports_against_p0_core(module_name):
    assert importlib.import_module(module_name)
