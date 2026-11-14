"""Compile a YAML declaration into a deterministic effective rule tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .clock import FixedClock
from .closure import (
    CYCLE,
    DROPPED,
    NOT_ALLOWED,
    OK,
    iter_candidates,
    resolve_merge_chains,
    table_in_scope,
)
from .errors import fail
from .normalizer import NormalizationPolicy
from .store import LedgerStore
from .util import display_path, read_text, stable_hash

DISPOSITIONS = ("drop", "merge", "whitelist_exempt")


def load_declaration(path: Path) -> dict[str, Any]:
    """Load and type-check a declaration without resolving any table."""

    path = Path(path)
    if not path.is_file():
        fail("E_IO", "declaration file does not exist", path=str(path))
    try:
        raw = yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        fail("E_DECL_INVALID", f"invalid YAML in {path.name}", reason=str(exc))
    if not isinstance(raw, Mapping):
        fail("E_DECL_INVALID", "declaration root must be a mapping")
    name = raw.get("name", path.stem)
    scope = raw.get("scope", "*")
    if not isinstance(name, str) or not name.strip():
        fail("E_DECL_INVALID", "declaration name must be a non-empty string")
    if not isinstance(scope, str) or not scope.strip():
        fail("E_DECL_INVALID", "scope must be a non-empty string")
    policy = NormalizationPolicy.from_mapping(raw.get("normalization"))

    raw_tables = raw.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        fail("E_DECL_INVALID", "tables must be a non-empty list")
    tables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_tables):
        if not isinstance(item, Mapping):
            fail("E_DECL_INVALID", "table reference must be a mapping", index=index)
        table_id = item.get("id")
        version = item.get("version")
        scopes = item.get("scopes", ["*"])
        if not isinstance(table_id, str) or not table_id:
            fail("E_DECL_INVALID", "table reference id must be a string", index=index)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            fail("E_DECL_INVALID", "table version must be a positive integer",
                 table=table_id)
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            fail("E_DECL_INVALID", "table scopes must be a string list", table=table_id)
        if table_id in seen:
            fail("E_DECL_INVALID", "duplicate table reference", table=table_id)
        seen.add(table_id)
        tables.append(
            {"id": table_id, "version": version, "scopes": list(scopes) or ["*"]}
        )

    priority = raw.get("priority", [ref["id"] for ref in tables])
    if not isinstance(priority, list) or not all(isinstance(i, str) for i in priority):
        fail("E_DECL_INVALID", "priority must be a list of table ids")
    merge_targets = raw.get("merge_targets")
    if merge_targets is not None:
        if not isinstance(merge_targets, list) or not all(
            isinstance(target, str) for target in merge_targets
        ):
            fail("E_DECL_INVALID", "merge_targets must be a list of strings")
        merge_targets = list(merge_targets)
    declaration = {
        "name": name.strip(),
        "scope": scope.strip(),
        "normalization": policy.to_dict(),
        "tables": tables,
        "priority": list(priority),
        "merge_targets": merge_targets,
    }
    declaration["declaration_path"] = display_path(path)
    declaration["policy"] = policy
    declaration["declaration_hash"] = stable_hash(declaration | {"policy": None})
    return declaration


def _fail_closure(term: str, resolution: dict[str, Any],
                  allowed: set[str] | None) -> None:
    details = {"term": term, "chain": resolution["chain"]}
    if resolution["status"] == CYCLE:
        fail("E_MERGE_CYCLE", "synonym merge chain loops back on itself",
             cycle=resolution["cycle"], **details)
    if resolution["status"] == DROPPED:
        fail("E_MERGE_TARGET_DROPPED",
             "merge chain ends at a term dropped by a higher-priority table",
             target=resolution["endpoint"],
             dropped_by=resolution["dropped_by"], **details)
    if resolution["status"] == NOT_ALLOWED:
        fail("E_MERGE_TARGET_NOT_ALLOWED",
             "merge target is outside declaration allow-list",
             target=resolution["endpoint"],
             allowed=sorted(allowed or ()), **details)
    fail("E_MERGE_TARGET_MISSING",
         "merge chain ends at a term absent from all tables",
         target=resolution["endpoint"], **details)


def build_rules(
    declaration: dict[str, Any],
    versions: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Resolve proposals by priority, then close merge targets transitively."""

    scope = declaration["scope"]
    refs = [ref for ref in declaration["tables"]
            if table_in_scope(scope, ref["scopes"])]
    active_ids = [ref["id"] for ref in refs]
    if len(set(declaration["priority"])) != len(declaration["priority"]):
        fail("E_DUP_PRIORITY", "priority list contains duplicate table ids",
             priority=declaration["priority"])
    known = {ref["id"] for ref in declaration["tables"]}
    unknown = [table_id for table_id in declaration["priority"] if table_id not in known]
    if unknown:
        fail("E_DECL_INVALID", "priority references undeclared tables", tables=unknown)
    missing_priority = [table_id for table_id in active_ids
                        if table_id not in declaration["priority"]]
    if missing_priority:
        fail("E_DECL_INVALID", "active tables are absent from priority",
             tables=missing_priority)

    table_trace: list[dict[str, Any]] = []
    for table_id in declaration["priority"]:
        if table_id not in active_ids:
            continue
        table = versions[table_id]
        table_trace.append(
            {
                "id": table["table_id"],
                "version": table["version"],
                "kind": table["kind"],
                "scopes": next(r["scopes"] for r in refs if r["id"] == table_id),
                "content_hash": table["content_hash"],
                "entry_count": table["entry_count"],
            }
        )

    proposals: dict[str, dict[str, Any]] = {}
    for term, candidate in iter_candidates(declaration, versions.values()):
        if term not in proposals:
            proposals[term] = candidate | {"term": term, "overridden_by": []}
            continue
        current = proposals[term]
        if (current["disposition"], current["target"]) != (
            candidate["disposition"], candidate["target"]
        ):
            current["overridden_by"].append(
                {
                    "disposition": candidate["disposition"],
                    "target": candidate["target"],
                    "source": candidate["source"],
                    "reason": "lower priority than winning proposal",
                }
            )
        else:
            current.setdefault("supporting_sources", []).append(candidate["source"])

    merge_targets = declaration["merge_targets"]
    allowed = (
        {declaration["policy"].normalize_validated(target)
         for target in merge_targets}
        if merge_targets is not None
        else None
    )
    resolutions = resolve_merge_chains(proposals, allowed)
    for term in sorted(resolutions):
        resolution = resolutions[term]
        if resolution["status"] != OK:
            _fail_closure(term, resolution, allowed)
        rule = proposals[term]
        rule["target"] = resolution["endpoint"]
        rule["merge_chain"] = resolution["chain"]

    rules = sorted(proposals.values(), key=lambda rule: rule["term"])
    counts = {name: 0 for name in DISPOSITIONS}
    for rule in rules:
        counts[rule["disposition"]] += 1
    return rules, counts, table_trace


def build_compilation(store: LedgerStore, declaration_path: Path) -> dict[str, Any]:
    declaration = load_declaration(declaration_path)
    scope = declaration["scope"]
    active_refs = [ref for ref in declaration["tables"]
                   if table_in_scope(scope, ref["scopes"])]
    versions: dict[str, dict[str, Any]] = {}
    for ref in active_refs:
        version = store.get_version(ref["id"], ref["version"])
        if version is None:
            fail(
                "E_REF_NOT_FOUND",
                "declared table version does not exist",
                table_id=ref["id"],
                version=ref["version"],
            )
        versions[ref["id"]] = version
    rules, counts, table_trace = build_rules(declaration, versions)
    rules_hash = stable_hash(
        {
            "scope": scope,
            "normalization": declaration["normalization"],
            "priority": [t["id"] for t in table_trace],
            "tables": [
                {"id": t["id"], "version": t["version"], "kind": t["kind"],
                 "content_hash": t["content_hash"]}
                for t in table_trace
            ],
            "rules": rules,
        }
    )
    return {
        "kind": "compilation",
        "schema_version": 1,
        "name": declaration["name"],
        "declaration_path": declaration["declaration_path"],
        "declaration_hash": declaration["declaration_hash"],
        "scope": scope,
        "normalization": declaration["normalization"],
        "priority": [t["id"] for t in table_trace],
        "tables": table_trace,
        "merge_targets": declaration["merge_targets"],
        "rules": rules,
        "rule_count": len(rules),
        "disposition_counts": counts,
        "rules_hash": rules_hash,
    }


def compile_declaration(
    store: LedgerStore,
    declaration_path: Path,
    clock: FixedClock,
    operator: str = "op-demo-01",
) -> dict[str, Any]:
    payload = build_compilation(store, declaration_path)
    saved = store.save_compilation(payload, operator, clock.timestamp())
    store.add_audit(
        "compilation.create",
        f"{payload['name']}:{payload['rules_hash'][:12]}",
        {"rule_count": payload["rule_count"], "scope": payload["scope"]},
        operator,
        clock.timestamp(),
    )
    return saved
