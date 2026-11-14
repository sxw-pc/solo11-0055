"""Eight deterministic consistency and conflict checks."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .clock import FixedClock
from .compiler import load_declaration, table_in_scope
from .normalizer import NormalizationPolicy
from .store import LedgerStore
from .util import canonical_json, stable_hash

CHECK_NAMES = {
    "C01": "contradictory_disposition",
    "C02": "merge_cycle",
    "C03": "merge_target_missing",
    "C04": "case_variant_duplicate",
    "C05": "missing_table_version",
    "C06": "duplicate_priority",
    "C07": "empty_table_version",
    "C08": "whitelist_overridden",
}
_WS = re.compile(r"\s+")

def _finding(code: str, subject: str, message: str, details: dict[str, Any],
             severity: str = "warning") -> dict[str, Any]:
    return {
        "code": code,
        "name": CHECK_NAMES[code],
        "severity": severity,
        "subject": subject,
        "message": message,
        "details": details,
    }

def _canonical_variant(term: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", term).casefold()).strip()

def _cycle_signature(cycle: list[str]) -> tuple[str, ...]:
    pivot = min(range(len(cycle)), key=lambda index: cycle[index])
    rotated = cycle[pivot:] + cycle[:pivot]
    return tuple(rotated)

def _find_cycles(graph: dict[str, str]) -> list[list[str]]:
    cycles: set[tuple[str, ...]] = set()
    for start in sorted(graph):
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in graph:
            if current in positions:
                cycle = path[positions[current]:]
                cycles.add(_cycle_signature(cycle))
                break
            positions[current] = len(path)
            path.append(current)
            current = graph[current]
    return [list(cycle) for cycle in sorted(cycles)]

def _active_versions(
    store: LedgerStore, declaration: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scope = declaration["scope"]
    versions: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for ref in declaration["tables"]:
        if not table_in_scope(scope, ref["scopes"]):
            continue
        version = store.get_version(ref["id"], ref["version"])
        if version is None:
            missing.append(ref)
        else:
            versions.append(version)
    return versions, missing

def run_checks(declaration: dict[str, Any],
               versions: list[dict[str, Any]],
               missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policy: NormalizationPolicy = declaration["policy"]
    findings: list[dict[str, Any]] = [
        _finding("C05", f"{ref['id']}@v{ref['version']}",
                 "declaration references a table version that is not registered",
                 {"table_id": ref["id"], "version": ref["version"]}, "error")
        for ref in missing
    ]

    priority_counts = defaultdict(int)
    for table_id in declaration["priority"]:
        priority_counts[table_id] += 1
    for table_id in sorted(priority_counts):
        if priority_counts[table_id] > 1:
            findings.append(_finding(
                "C06", table_id, "priority list contains a duplicate table id",
                {"table_id": table_id,
                 "occurrences": priority_counts[table_id]}, "error"))

    for version in versions:
        if version["entry_count"] == 0:
            findings.append(_finding(
                "C07", f"{version['table_id']}@v{version['version']}",
                "referenced table version has no entries",
                {"table_id": version["table_id"],
                 "version": version["version"]}, "error"))

    proposals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    whitelist_terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    drop_terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    graph: dict[str, str] = {}
    defined = {policy.normalize_validated(target)
               for target in (declaration["merge_targets"] or [])}
    dispositions = {
        "synonym": "merge", "stoplist": "drop", "noise": "drop",
        "whitelist": "whitelist_exempt",
    }
    for version in versions:
        for entry in version["entries"]:
            term = policy.normalize_validated(entry["raw"])
            defined.add(term)
            source = {"table_id": version["table_id"],
                      "version": version["version"], "kind": version["kind"],
                      "line": entry["line"], "raw": entry["raw"]}
            kind = version["kind"]
            target = (policy.normalize_validated(entry["target_raw"])
                      if kind == "synonym" else None)
            if kind == "synonym":
                graph[term] = target
            proposals[term].append(
                source | {"disposition": dispositions[kind], "target": target})
            if kind in {"stoplist", "noise"}:
                drop_terms[term].append(source)
            elif kind == "whitelist":
                whitelist_terms[term].append(source)

    for term in sorted(proposals):
        conflicts = [item for item in proposals[term]
                     if item["kind"] != "whitelist"]
        signatures = {(item["disposition"], item["target"])
                      for item in conflicts}
        if len(signatures) > 1:
            findings.append(_finding(
                "C01", term,
                "the same term is disposed differently by multiple tables",
                {"term": term, "proposals": sorted(
                    conflicts, key=lambda item: (item["table_id"],
                                                 item["version"], item["line"]))}
            ))

    for cycle in _find_cycles(graph):
        findings.append(_finding(
            "C02", " -> ".join(cycle + [cycle[0]]),
            "synonym mapping contains a directed cycle",
            {"cycle": cycle + [cycle[0]]}, "error"))

    for term, target in sorted(graph.items()):
        if target not in defined:
            findings.append(_finding(
                "C03", target,
                "synonym target is absent from all referenced tables and allow-lists",
                {"term": term, "target": target}, "error"))

    groups: dict[str, set[str]] = defaultdict(set)
    group_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for version in versions:
        for entry in version["entries"]:
            key = _canonical_variant(entry["raw"])
            groups[key].add(policy.normalize_validated(entry["raw"]))
            group_sources[key].append(
                {"table_id": version["table_id"], "version": version["version"],
                 "raw": entry["raw"]})
    for key in sorted(groups):
        if len(groups[key]) > 1:
            findings.append(_finding(
                "C04", key,
                "case variants normalize to distinct terms under the declaration",
                {"variants": sorted(groups[key]),
                 "sources": sorted(group_sources[key],
                                   key=lambda item: (item["table_id"],
                                                     item["raw"]))}))

    for term in sorted(set(whitelist_terms) & set(drop_terms)):
        findings.append(_finding(
            "C08", term,
            "a whitelist term is also covered by a stop or noise table",
            {"term": term, "whitelist": whitelist_terms[term],
             "drop_tables": drop_terms[term]}))

    unique = {canonical_json(finding): finding for finding in findings}
    return sorted(
        unique.values(),
        key=lambda finding: (finding["code"], finding["subject"],
                             canonical_json(finding["details"])),
    )

def check_declaration(
    store: LedgerStore,
    declaration_path: Path,
    clock: FixedClock,
    operator: str = "op-demo-01",
) -> dict[str, Any]:
    declaration = load_declaration(declaration_path)
    versions, missing = _active_versions(store, declaration)
    findings = run_checks(declaration, versions, missing)
    by_code = dict(sorted(Counter(f["code"] for f in findings).items()))
    payload: dict[str, Any] = {
        "kind": "check",
        "schema_version": 1,
        "declaration_path": declaration["declaration_path"],
        "declaration_hash": declaration["declaration_hash"],
        "scope": declaration["scope"],
        "status": "pass" if not findings else "issues",
        "findings": findings,
        "summary": {"total_issues": len(findings),
                    "by_code": dict(sorted(by_code.items()))},
        "generated_at": clock.timestamp(),
    }
    payload["report_hash"] = stable_hash(payload)
    saved = store.save_check(
        declaration["declaration_path"], declaration["declaration_hash"],
        payload, operator, clock.timestamp(),
    )
    store.add_audit(
        "check.run",
        declaration["name"],
        {"total_issues": len(findings), "by_code": payload["summary"]["by_code"]},
        operator,
        clock.timestamp(),
    )
    return saved
