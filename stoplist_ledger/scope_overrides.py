"""Scope override layer: parsing and the deterministic declaration merge.

Merge contract (version 1), also recorded in each scoped compilation. Tables
are keyed by id: override entries replace the base row, new ids add a row, and
``{"id": X, "remove": true}`` removes it. ``priority`` is whole-list
replacement when present; ``merge_targets`` is an ordered union (null resets).
Unknown ids fail with ``E_DECL_INVALID`` naming the layer and the id.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import fail

BASE_LAYER = "base"
OVERRIDE_LAYER = "override"
REMOVE_MARKER = "remove"
CONTRACT_VERSION = 1

def parse_table_ref(item: Any, index: int, layer: str) -> dict[str, Any] | None:
    """Validate one table reference; returns None for a removal marker."""

    if not isinstance(item, Mapping):
        fail("E_DECL_INVALID", "table reference must be a mapping",
             layer=layer, index=index)
    table_id = item.get("id")
    if not isinstance(table_id, str) or not table_id:
        fail("E_DECL_INVALID", "table reference id must be a string",
             layer=layer, index=index)
    if item.get(REMOVE_MARKER) is True:
        return None
    version, scopes = item.get("version"), item.get("scopes", ["*"])
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        fail("E_DECL_INVALID", "table version must be a positive integer",
             layer=layer, table=table_id)
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        fail("E_DECL_INVALID", "table scopes must be a string list",
             layer=layer, table=table_id)
    return {"id": table_id, "version": version,
            "scopes": list(scopes) or ["*"]}

def _string_list(value: Any, field: str, scope: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        fail("E_SCOPE_OVERRIDE_INVALID",
             f"scope override field {field} must be a string list",
             scope=scope, field=field)
    return list(value)

def parse_scope_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    """Type-check the ``scope_overrides`` mapping without resolving tables."""

    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        fail("E_SCOPE_OVERRIDE_INVALID",
             "scope_overrides must be a mapping of scope name to override layer")
    parsed: dict[str, dict[str, Any]] = {}
    for raw_scope, layer in raw.items():
        if not isinstance(raw_scope, str) or not raw_scope.strip():
            fail("E_SCOPE_OVERRIDE_INVALID",
                 "scope override keys must be non-empty strings")
        scope = raw_scope.strip()
        if not isinstance(layer, Mapping):
            fail("E_SCOPE_OVERRIDE_INVALID",
                 "scope override layer must be a mapping", scope=scope)
        unknown = set(layer) - {"tables", "priority", "merge_targets"}
        if unknown:
            fail("E_SCOPE_OVERRIDE_INVALID",
                 "scope override contains unsupported fields",
                 scope=scope, fields=sorted(unknown))
        entry: dict[str, Any] = {}
        if "tables" in layer:
            if not isinstance(layer["tables"], list) or not layer["tables"]:
                fail("E_SCOPE_OVERRIDE_INVALID",
                     "override tables must be a non-empty list", scope=scope)
            refs, seen = [], set()
            for index, item in enumerate(layer["tables"]):
                ref = parse_table_ref(item, index, OVERRIDE_LAYER)
                table_id = item.get("id") if isinstance(item, Mapping) else None
                if table_id in seen:
                    fail("E_SCOPE_OVERRIDE_INVALID",
                         "duplicate table reference in override",
                         scope=scope, table=table_id)
                seen.add(table_id)
                refs.append(ref if ref is not None
                            else {REMOVE_MARKER: True, "id": table_id})
            entry["tables"] = refs
        if "priority" in layer:
            entry["priority"] = _string_list(layer["priority"], "priority", scope)
        if "merge_targets" in layer:
            raw_targets = layer["merge_targets"]
            entry["merge_targets"] = (
                None if raw_targets is None
                else _string_list(raw_targets, "merge_targets", scope))
        parsed[scope] = entry
    return parsed

def _fail_unknown_priority(scope: str, priority: list[str], known: set[str],
                           layer: str) -> None:
    unknown = [table_id for table_id in priority if table_id not in known]
    if unknown:
        fail("E_DECL_INVALID",
             "priority references tables absent from the merged declaration",
             scope=scope, layer=layer, tables=unknown)
    if len(set(priority)) != len(priority):
        fail("E_DUP_PRIORITY", "priority list contains duplicate table ids",
             scope=scope, priority=priority)

def effective_declaration(declaration: dict[str, Any],
                          scope: str) -> dict[str, Any] | None:
    """Merge the base declaration with the override layer for ``scope``.

    Returns None when no override layer exists. The result keeps the base
    declaration shape so the compiler stays layer-agnostic; ``table_layers``
    and ``merge_contract`` carry provenance.
    """

    override = (declaration.get("scope_overrides") or {}).get(scope)
    if override is None:
        return None

    tables = [dict(ref) for ref in declaration["tables"]]
    layers = {ref["id"]: BASE_LAYER for ref in declaration["tables"]}
    operations: dict[str, str] = {}
    removed: dict[str, str] = {}
    for ref in override.get("tables", []):
        table_id = ref["id"]
        if REMOVE_MARKER in ref:
            if table_id not in layers:
                fail("E_DECL_INVALID",
                     "override removes a table not declared by the base layer",
                     scope=scope, layer=OVERRIDE_LAYER, table=table_id)
            tables = [item for item in tables if item["id"] != table_id]
            removed[table_id] = layers.pop(table_id)
            operations[table_id] = "remove"
            continue
        operations[table_id] = "replace" if table_id in layers else "add"
        tables = [ref if item["id"] == table_id else item for item in tables] \
            if table_id in layers else tables + [dict(ref)]
        layers[table_id] = OVERRIDE_LAYER

    known = {ref["id"] for ref in tables}
    has_priority = "priority" in override
    priority = list(override["priority"]) if has_priority \
        else list(declaration["priority"])
    _fail_unknown_priority(scope, priority, known,
                           OVERRIDE_LAYER if has_priority else BASE_LAYER)
    missing = sorted(known - set(priority))
    if missing:
        fail("E_DECL_INVALID",
             "merged tables are absent from the effective priority",
             scope=scope, tables=missing)

    if "merge_targets" in override:
        if override["merge_targets"] is None:
            merge_targets, mt_semantics = None, "reset"
        else:
            merge_targets = list(dict.fromkeys(
                (declaration["merge_targets"] or []) + override["merge_targets"]
            ))
            mt_semantics = "union"
    else:
        merge_targets, mt_semantics = declaration["merge_targets"], "base"

    effective = {key: value for key, value in declaration.items()
                 if key != "scope_overrides"}
    effective.update(scope=scope, tables=tables, priority=priority,
                     merge_targets=merge_targets, table_layers=layers,
                     merge_contract={
                         "contract_version": CONTRACT_VERSION,
                         "scope": scope,
                         "tables": {
                             table_id: {"operation": operations.get(table_id,
                                                                    "base"),
                                        "layer": layers[table_id]}
                             for table_id in sorted(layers)
                         } | {
                             table_id: {"operation": "remove", "layer": layer}
                             for table_id, layer in sorted(removed.items())
                         },
                         "priority": {
                             "semantics": "replace" if has_priority else "inherit",
                             "layer": OVERRIDE_LAYER if has_priority else BASE_LAYER,
                         },
                         "merge_targets": {"semantics": mt_semantics},
                     })
    return effective
