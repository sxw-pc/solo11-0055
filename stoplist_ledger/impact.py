"""Deterministic A/B impact analysis over rule trees and snapshots."""

from __future__ import annotations

from typing import Any, Iterable

from .clock import FixedClock
from .errors import fail
from .store import LedgerStore
from .util import stable_hash

KEEP = {"disposition": "keep", "target": None}


def _outcome(rule: dict[str, Any] | None) -> dict[str, Any]:
    if rule is None:
        return dict(KEEP)
    return {"disposition": rule["disposition"], "target": rule["target"]}


def compare_rule_sets(left_rules: list[dict[str, Any]],
                      right_rules: list[dict[str, Any]]
                      ) -> dict[str, list[dict[str, Any]]]:
    left = {rule["term"]: rule for rule in left_rules}
    right = {rule["term"]: rule for rule in right_rules}
    terms = sorted(set(left) | set(right))
    new_dropped: list[dict[str, Any]] = []
    gone_dropped: list[dict[str, Any]] = []
    merge_changes: list[dict[str, Any]] = []
    exemption_changes: list[dict[str, Any]] = []
    for term in terms:
        before, after = _outcome(left.get(term)), _outcome(right.get(term))
        if after["disposition"] == "drop" and before["disposition"] != "drop":
            new_dropped.append({"term": term, "before": before, "after": after})
        if before["disposition"] == "drop" and after["disposition"] != "drop":
            gone_dropped.append({"term": term, "before": before, "after": after})
        if (before["disposition"] == "merge"
                or after["disposition"] == "merge") \
                and before["target"] != after["target"]:
            merge_changes.append({"term": term, "before": before, "after": after})
        if (before["disposition"] == "whitelist_exempt"
                ) != (after["disposition"] == "whitelist_exempt"):
            exemption_changes.append({"term": term, "before": before, "after": after})
    return {
        "new_dropped": new_dropped,
        "gone_dropped": gone_dropped,
        "merge_target_changes": merge_changes,
        "exemption_changes": exemption_changes,
    }


def _token_outcome(token: dict[str, Any]) -> dict[str, Any]:
    return {"disposition": token["disposition"], "target": token["target"]}


def compare_texts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_tokens = left["tokens"]
    right_tokens = right["tokens"]
    changes: list[dict[str, Any]] = []
    for index in range(max(len(left_tokens), len(right_tokens))):
        before = _token_outcome(left_tokens[index]) if index < len(left_tokens) else None
        after = _token_outcome(right_tokens[index]) if index < len(right_tokens) else None
        if before != after:
            token = right_tokens[index] if index < len(right_tokens) else left_tokens[index]
            changes.append({"index": index, "raw": token["raw"],
                            "term": token["term"], "before": before,
                            "after": after})
    if left.get("text_hash") != right.get("text_hash"):
        changes = [
            {"index": token["index"], "raw": token["raw"], "term": token["term"],
             "before": _token_outcome(left["tokens"][token["index"]])
             if token["index"] < len(left["tokens"]) else None,
             "after": _token_outcome(token)}
            for token in right["tokens"]
        ]
    return {
        "text_id": right["text_id"],
        "affected": bool(changes),
        "affected_tokens": len(changes),
        "before_hash": left["result_hash"],
        "after_hash": right["result_hash"],
        "changes": changes,
    }


def _snapshot_map(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda item: (item["text_id"], item["id"])):
        mapping.setdefault(record["text_id"], record)
    return mapping


def _side(store: LedgerStore, snapshot_ids: list[int]
          ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = []
    for snapshot_id in snapshot_ids:
        record = store.get_snapshot(snapshot_id)
        if record is None:
            fail("E_REF_NOT_FOUND", "snapshot does not exist",
                 snapshot_id=snapshot_id)
        records.append(record)
    records.sort(key=lambda item: (item["text_id"], item["id"]))
    compilation_ids = {record["compilation_id"] for record in records}
    if len(compilation_ids) != 1:
        fail("E_BAD_FORMAT", "an impact side must use a single compilation",
             compilation_ids=sorted(compilation_ids))
    compilation_id = next(iter(compilation_ids))
    compilation = store.get_compilation(compilation_id)
    if compilation is None:
        fail("E_REF_NOT_FOUND", "snapshot compilation is missing",
             compilation_id=compilation_id)
    return compilation, records


def build_impact_payload(store: LedgerStore, left_snapshot_ids: list[int],
                         right_snapshot_ids: list[int],
                         generated_at: str) -> dict[str, Any]:
    if not left_snapshot_ids or not right_snapshot_ids:
        fail("E_BAD_FORMAT", "both impact sides require at least one snapshot")
    left_comp, left_records = _side(store, list(left_snapshot_ids))
    right_comp, right_records = _side(store, list(right_snapshot_ids))
    rule_changes = compare_rule_sets(left_comp["rules"], right_comp["rules"])
    left_map, right_map = _snapshot_map(left_records), _snapshot_map(right_records)
    text_changes = [
        compare_texts(left_map[text_id], right_map[text_id])
        for text_id in sorted(set(left_map) & set(right_map))
    ]
    summary = {
        "new_dropped_terms": len(rule_changes["new_dropped"]),
        "gone_dropped_terms": len(rule_changes["gone_dropped"]),
        "merge_target_changes": len(rule_changes["merge_target_changes"]),
        "exemption_changes": len(rule_changes["exemption_changes"]),
        "affected_tokens": sum(item["affected_tokens"] for item in text_changes),
        "affected_texts": sum(1 for item in text_changes if item["affected"]),
    }
    payload = {
        "kind": "impact",
        "schema_version": 1,
        "left": _side_meta(left_comp, left_records),
        "right": _side_meta(right_comp, right_records),
        "rule_changes": rule_changes,
        "text_changes": text_changes,
        "summary": summary,
        "generated_at": generated_at,
    }
    payload["report_hash"] = stable_hash(payload)
    return payload


def _side_meta(compilation: dict[str, Any],
               records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "compilation": {"name": compilation["name"],
                        "rules_hash": compilation["rules_hash"],
                        "scope": compilation["scope"]},
        "snapshots": [
            {"id": record["id"], "text_id": record["text_id"],
             "result_hash": record["result_hash"]}
            for record in records
        ],
    }


def create_impact(store: LedgerStore, left_snapshot_ids: list[int],
                  right_snapshot_ids: list[int], clock: FixedClock,
                  operator: str = "op-demo-01") -> dict[str, Any]:
    refs = (list(left_snapshot_ids), list(right_snapshot_ids))
    payload = build_impact_payload(store, refs[0], refs[1], clock.timestamp())
    saved = store.save_impact(refs[0], refs[1], payload, operator,
                              clock.timestamp())
    store.add_audit("impact.create", payload["report_hash"][:16],
                    {"summary": payload["summary"]}, operator, clock.timestamp())
    return saved
