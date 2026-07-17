import json

from memory_edges import MemoryEdgeStore


def test_memory_edge_store_deduplicates_and_selects_bidirectional_edges(tmp_path):
    store = MemoryEdgeStore({"state_dir": str(tmp_path)})

    store.add_edge("source", "target", "supports", 0.7, "first")
    store.add_edge("source", "target", "supports", 0.4, "weaker")
    store.add_edge("other", "source", "triggers", 2.0, "incoming")

    edges = store.list_edges()
    assert len(edges) == 2
    assert next(edge for edge in edges if edge["target"] == "target")["confidence"] == 0.7

    related = store.related_edges({"source"}, min_confidence=0.5, limit_per_source=3)
    assert {edge["target"] for edge in related} == {"target", "other"}
    assert next(edge for edge in related if edge["target"] == "other")["direction"] == "incoming"
    assert next(edge for edge in related if edge["target"] == "other")["confidence"] == 1.0


def test_memory_edge_store_normalizes_legacy_rows_and_deletes_bucket_edges(tmp_path):
    store = MemoryEdgeStore({"state_dir": str(tmp_path)})
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"source_memory_id": "a", "target_memory_id": "b", "type": "unknown"}) + "\n")
        handle.write("not-json\n")

    assert store.list_edges()[0]["relation_type"] == "relates_to"
    assert store.delete_for_bucket("a") == 1
    assert store.list_edges() == []
