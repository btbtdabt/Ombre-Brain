import json
import os
from datetime import datetime, timezone
from typing import Any

from edge_records import load_jsonl_records, upsert_by_confidence

from runtime_values import rounded_unit_float


RELATION_TYPES = {
    "triggers",
    "causes",
    "precedes",
    "context_of",
    "same_event",
    "updates",
    "next_context",
    "previous_context",
    "reflects_on",
    "contradicts",
    "supports",
    "promises",
    "blocks",
    "belongs_to",
    "emotional_echo",
    "evidenced_by",
    "relates_to",
}


class MemoryEdgeStore:
    """Small JSONL-backed store for explicit memory relationships."""

    def __init__(self, config: dict):
        state_dir = config.get("state_dir") or os.path.join(
            os.path.dirname(os.path.abspath(config.get("buckets_dir", "buckets"))),
            "state",
        )
        self.path = os.path.join(state_dir, "memory_edges.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def add_edge(
        self,
        source: str,
        target: str,
        relation_type: str,
        confidence: float = 0.5,
        reason: str = "",
        created_at: str | None = None,
    ) -> dict | None:
        source = str(source or "").strip()
        target = str(target or "").strip()
        relation_type = str(relation_type or "relates_to").strip()
        if not source or not target or source == target:
            return None
        if relation_type not in RELATION_TYPES:
            relation_type = "relates_to"

        edge = {
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "confidence": self._clamp(confidence),
            "reason": str(reason or "").strip()[:240],
            "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        edges = upsert_by_confidence(self.list_edges(), edge, self._same_edge)
        self._write_all(edges)
        return edge

    def add_edges(self, edges: list[dict[str, Any]]) -> list[dict]:
        saved = []
        for edge in edges or []:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source") or edge.get("source_memory_id")
            target = edge.get("target") or edge.get("target_memory_id")
            relation_type = edge.get("relation_type") or edge.get("type")
            if source is None or target is None:
                continue
            if relation_type is None:
                relation_type = "relates_to"
            saved_edge = self.add_edge(
                source,
                target,
                relation_type,
                edge.get("confidence", 0.5),
                edge.get("reason", ""),
                edge.get("created_at"),
            )
            if saved_edge:
                saved.append(saved_edge)
        return saved

    def list_edges(self) -> list[dict]:
        return load_jsonl_records(self.path, self._normalize)

    def related_edges(
        self,
        bucket_ids: list[str] | set[str],
        min_confidence: float = 0.55,
        limit_per_source: int = 1,
    ) -> list[dict]:
        ids = {str(bucket_id) for bucket_id in bucket_ids if bucket_id}
        if not ids:
            return []
        grouped: dict[str, list[dict]] = {}
        for edge in self.list_edges():
            if edge["confidence"] < min_confidence:
                continue
            if edge["source"] in ids:
                grouped.setdefault(edge["source"], []).append(edge)
            elif edge["target"] in ids:
                flipped = dict(edge)
                flipped["source"], flipped["target"] = edge["target"], edge["source"]
                flipped["direction"] = "incoming"
                grouped.setdefault(flipped["source"], []).append(flipped)

        selected = []
        for source, edges in grouped.items():
            edges.sort(key=lambda item: item.get("confidence", 0), reverse=True)
            selected.extend(edges[: max(1, limit_per_source)])
        selected.sort(key=lambda item: item.get("confidence", 0), reverse=True)
        return selected

    def delete_for_bucket(self, bucket_id: str) -> int:
        bucket_id = str(bucket_id or "").strip()
        if not bucket_id:
            return 0
        edges = self.list_edges()
        kept = [
            edge for edge in edges
            if edge.get("source") != bucket_id and edge.get("target") != bucket_id
        ]
        deleted = len(edges) - len(kept)
        if deleted:
            self._write_all(kept)
        return deleted

    def _write_all(self, edges: list[dict]) -> None:
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            for edge in edges:
                f.write(json.dumps(edge, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp_path, self.path)

    def _normalize(self, edge: dict) -> dict | None:
        source = str(edge.get("source") or edge.get("source_memory_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_memory_id") or "").strip()
        if not source or not target or source == target:
            return None
        relation_type = str(edge.get("relation_type") or edge.get("type") or "relates_to").strip()
        if relation_type not in RELATION_TYPES:
            relation_type = "relates_to"
        return {
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "confidence": self._clamp(edge.get("confidence", 0.5)),
            "reason": str(edge.get("reason") or "").strip()[:240],
            "created_at": str(edge.get("created_at") or ""),
        }

    @staticmethod
    def _same_edge(left: dict, right: dict) -> bool:
        return (
            left.get("source") == right.get("source")
            and left.get("target") == right.get("target")
            and left.get("relation_type") == right.get("relation_type")
        )

    _clamp = staticmethod(rounded_unit_float)
