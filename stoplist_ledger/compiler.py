"""Compile a YAML declaration (with `extends` inheritance) into a rule tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .clock import FixedClock
from .errors import fail
from .inheritance import load_chain, merge_chain
from .normalizer import NormalizationPolicy
from .store import LedgerStore
from .util import stable_hash

DISPOSITIONS = ("drop", "merge", "whitelist_exempt")


def load_declaration(path: Path) -> dict[str, Any]:
    """Load, inherit, merge and type-check a declaration chain."""

    return merge_chain(load_chain(Path(path)))


def table_in_scope(scope: str, scopes: list[str]) -> bool:
    if scope in {"*", "all"}:
        return True
    return any(item in {"*", "all", scope} for item in scopes)


def _proposal(source: dict[str, Any], disposition: str,
              target: str | None = None) -> dict[str, Any]:
    return {"disposition": disposition, "target": target,
            "source": source | {"rule": f"{source['kind']}:{disposition}"}}


def _source(table: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    return {"table_id": table["table_id"], "version": table["version"],
            "kind": table["kind"], "line": entry["line"], "raw": entry["raw"]}


def _fail_empty_scope(declaration: dict[str, Any]) -> None:
    origins = declaration.get("field_origins", {}).get("tables", {})
    inherited = declaration.get("chain_depth", 1) > 1
    message = ("priority covers no effective table: every table inherited "
               "from a parent layer is filtered out by the effective scope"
               if inherited else
               "no table survives scope filtering under the declared scope")
    tables = [
        {"id": ref["id"], "scopes": ref["scopes"],
         "origin_layer": origins.get(ref["id"], {}).get("layer", 0),
         "origin_layer_name": origins.get(ref["id"], {}).get(
             "layer_name", declaration["name"])}
        for ref in declaration["tables"]]
    fail("E_DECL_INVALID", message, scope=declaration["scope"],
         inherited=inherited, tables=tables)


def build_rules(
    declaration: dict[str, Any],
    versions: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Resolve proposals by declared priority; first proposal wins."""

    policy: NormalizationPolicy = declaration["policy"]
    refs = [ref for ref in declaration["tables"]
            if table_in_scope(declaration["scope"], ref["scopes"])]
    active_ids = [ref["id"] for ref in refs]
    if not active_ids:
        _fail_empty_scope(declaration)
    if len(set(declaration["priority"])) != len(declaration["priority"]):
        fail("E_DUP_PRIORITY", "priority list contains duplicate table ids",
             priority=declaration["priority"])
    known = {ref["id"] for ref in declaration["tables"]}
    unknown = [table_id for table_id in declaration["priority"]
               if table_id not in known]
    if unknown:
        fail("E_DECL_INVALID", "priority references undeclared tables", tables=unknown)
    missing = [table_id for table_id in active_ids
               if table_id not in declaration["priority"]]
    if missing:
        fail("E_DECL_INVALID", "active tables are absent from priority", tables=missing)

    proposals: dict[str, dict[str, Any]] = {}
    table_trace: list[dict[str, Any]] = []
    for table_id in declaration["priority"]:
        if table_id not in active_ids:
            continue
        table = versions[table_id]
        table_trace.append({
            "id": table["table_id"], "version": table["version"],
            "kind": table["kind"],
            "scopes": next(r["scopes"] for r in refs if r["id"] == table_id),
            "content_hash": table["content_hash"],
            "entry_count": table["entry_count"]})
        for entry in table["entries"]:
            term = policy.normalize_validated(entry["raw"])
            source = _source(table, entry)
            if table["kind"] in {"stoplist", "noise"}:
                candidate = _proposal(source, "drop")
            elif table["kind"] == "whitelist":
                candidate = _proposal(source, "whitelist_exempt")
            else:
                candidate = _proposal(
                    source, "merge", policy.normalize_validated(entry["target_raw"]))
            if term not in proposals:
                proposals[term] = candidate | {"term": term, "overridden_by": []}
                continue
            current = proposals[term]
            if (current["disposition"], current["target"]) != (
                    candidate["disposition"], candidate["target"]):
                current["overridden_by"].append(
                    candidate | {"reason": "lower priority than winning proposal"})
            else:
                current.setdefault("supporting_sources", []).append(
                    candidate["source"])

    if declaration["merge_targets"] is not None:
        allowed = {policy.normalize_validated(target)
                   for target in declaration["merge_targets"]}
        for rule in proposals.values():
            if rule["disposition"] == "merge" and rule["target"] not in allowed:
                fail("E_MERGE_TARGET_NOT_ALLOWED",
                     "merge target is outside declaration allow-list",
                     term=rule["term"], target=rule["target"],
                     allowed=sorted(allowed))
    rules = sorted(proposals.values(), key=lambda rule: rule["term"])
    counts = {name: 0 for name in DISPOSITIONS}
    for rule in rules:
        counts[rule["disposition"]] += 1
    return rules, counts, table_trace


def build_compilation(store: LedgerStore, declaration_path: Path) -> dict[str, Any]:
    declaration = load_declaration(declaration_path)
    active_refs = [ref for ref in declaration["tables"]
                   if table_in_scope(declaration["scope"], ref["scopes"])]
    versions = {}
    for ref in active_refs:
        version = store.get_version(ref["id"], ref["version"])
        if version is None:
            fail("E_REF_NOT_FOUND", "declared table version does not exist",
                 table_id=ref["id"], version=ref["version"])
        versions[ref["id"]] = version
    rules, counts, table_trace = build_rules(declaration, versions)
    identity: dict[str, Any] = {
        "scope": declaration["scope"],
        "normalization": declaration["normalization"],
        "priority": [t["id"] for t in table_trace],
        "tables": [{"id": t["id"], "version": t["version"], "kind": t["kind"],
                    "content_hash": t["content_hash"]} for t in table_trace],
        "rules": rules}
    if declaration["chain_depth"] > 1:
        identity["inheritance_chain"] = [
            {"path": item["path"], "file_sha256": item["file_sha256"]}
            for item in declaration["resolved_chain"]]
    return {
        "kind": "compilation", "schema_version": 1,
        "name": declaration["name"],
        "declaration_path": declaration["declaration_path"],
        "declaration_hash": declaration["declaration_hash"],
        "scope": declaration["scope"],
        "normalization": declaration["normalization"],
        "priority": [t["id"] for t in table_trace], "tables": table_trace,
        "merge_targets": declaration["merge_targets"], "rules": rules,
        "rule_count": len(rules), "disposition_counts": counts,
        "resolved_chain": declaration["resolved_chain"],
        "field_origins": declaration["field_origins"],
        "rules_hash": stable_hash(identity)}


def compile_declaration(
    store: LedgerStore, declaration_path: Path, clock: FixedClock,
    operator: str = "op-demo-01",
) -> dict[str, Any]:
    payload = build_compilation(store, declaration_path)
    saved = store.save_compilation(payload, operator, clock.timestamp())
    store.add_audit(
        "compilation.create",
        f"{payload['name']}:{payload['rules_hash'][:12]}",
        {"rule_count": payload["rule_count"], "scope": payload["scope"],
         "chain_depth": len(payload["resolved_chain"])},
        operator, clock.timestamp())
    return saved
