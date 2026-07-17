from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_one_click_installer_targets_the_p0luz_layout() -> None:
    script = (ROOT / "scripts" / "one_click.sh").read_text(encoding="utf-8")

    assert 'OMBRE_SERVICE: brain' in script
    assert 'OMBRE_SERVICE: gateway' in script
    assert 'OMBRE_PERSIST_CODE: 0' in script
    assert 'src/server.py' in script
    assert 'src/gateway.py' in script


def test_current_maintenance_and_alignment_tools_are_present() -> None:
    required = {
        "check_production_alignment.py",
        "audit_entity_edges.py",
        "build_moment_graph.py",
        "build_word_map.py",
        "cleanup_duplicate_buckets.py",
        "cleanup_orphan_embeddings.py",
        "migrate_bucket_files.py",
        "migrate_darkroom_active_room.py",
        "sync_to_supabase.py",
    }
    actual = {path.name for path in (ROOT / "scripts").iterdir() if path.is_file()}

    assert required <= actual
