"""Declaration inheritance: extend-chain resolution and field-aware merging."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import fail
from .normalizer import NormalizationPolicy
from .util import display_path, file_sha256, read_text, stable_hash

OVERRIDE_FIELDS = ("scope", "normalization", "tables", "priority", "merge_targets")
ROOT_KEYS = set(OVERRIDE_FIELDS) | {"name", "extends", "overrides"}
SCALAR_FIELDS = ("scope", "normalization", "priority", "merge_targets")


def read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        raw = yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        fail("E_DECL_INVALID", f"invalid YAML in {path.name}", reason=str(exc))
    if not isinstance(raw, Mapping):
        fail("E_DECL_INVALID", "declaration root must be a mapping",
             path=display_path(path))
    return raw


def validate_tables(raw_tables: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tables, list) or not raw_tables:
        fail("E_DECL_INVALID", "tables must be a non-empty list")
    tables, seen = [], set()
    for index, item in enumerate(raw_tables):
        if not isinstance(item, Mapping):
            fail("E_DECL_INVALID", "table reference must be a mapping", index=index)
        table_id, version, scopes = (
            item.get("id"), item.get("version"), item.get("scopes", ["*"]))
        if not isinstance(table_id, str) or not table_id:
            fail("E_DECL_INVALID", "table reference id must be a string",
                 index=index)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            fail("E_DECL_INVALID", "table version must be a positive integer",
                 table=table_id)
        if not isinstance(scopes, list) or not scopes or not all(
            isinstance(scope, str) and scope for scope in scopes
        ):
            fail("E_DECL_INVALID", "table scopes must be a non-empty string list",
                 table=table_id)
        if table_id in seen:
            fail("E_DECL_INVALID", "duplicate table reference", table=table_id)
        seen.add(table_id)
        tables.append({"id": table_id, "version": version, "scopes": list(scopes)})
    return tables


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        fail("E_DECL_INVALID",
             "priority must be a list of table ids" if label == "priority"
             else f"{label} must be a list of strings")
    return list(value)


def _check_name(raw: Mapping[str, Any], path: Path) -> str:
    name = raw.get("name", path.stem)
    if not isinstance(name, str) or not name.strip():
        fail("E_DECL_INVALID", "declaration name must be a non-empty string")
    return name.strip()


def _policy(raw: Mapping[str, Any]) -> dict[str, str]:
    return NormalizationPolicy.from_mapping(raw.get("normalization")).to_dict()


def root_layer(path: Path) -> dict[str, Any]:
    """Parse and type-check a standalone (non-extending) declaration file."""

    raw = read_yaml(path)
    unknown = set(raw) - (ROOT_KEYS - {"extends", "overrides"})
    if unknown:
        fail("E_DECL_INVALID", "unknown declaration keys", keys=sorted(unknown))
    scope = raw.get("scope", "*")
    if not isinstance(scope, str) or not scope.strip():
        fail("E_DECL_INVALID", "scope must be a non-empty string")
    tables = validate_tables(raw.get("tables"))
    return {
        "path": path, "name": _check_name(raw, path), "scope": scope.strip(),
        "normalization": _policy(raw), "tables": tables,
        "priority": _string_list(raw.get("priority", [r["id"] for r in tables]),
                                 "priority"),
        "merge_targets": (_string_list(raw["merge_targets"], "merge_targets")
                          if raw.get("merge_targets") is not None else None)}


def child_layer(path: Path) -> dict[str, Any]:
    """Parse and type-check an extending declaration (diffs only)."""

    raw = read_yaml(path)
    unknown = set(raw) - ROOT_KEYS
    if unknown:
        fail("E_DECL_INVALID", "unknown declaration keys", keys=sorted(unknown))
    misplaced = set(raw) & set(OVERRIDE_FIELDS)
    if misplaced:
        fail("E_DECL_INVALID",
             "child declarations express differences only under overrides",
             keys=sorted(misplaced))
    extends = raw.get("extends")
    if not isinstance(extends, str) or not extends.strip():
        fail("E_DECL_INVALID", "extends must be a non-empty path string",
             path=display_path(path))
    overrides = raw.get("overrides", {})
    if not isinstance(overrides, Mapping):
        fail("E_DECL_INVALID", "overrides must be a mapping")
    bad = set(overrides) - set(OVERRIDE_FIELDS)
    if bad:
        fail("E_DECL_INVALID", "unknown override fields", keys=sorted(bad))
    parsed: dict[str, Any] = {}
    if "scope" in overrides:
        scope = overrides["scope"]
        if not isinstance(scope, str) or not scope.strip():
            fail("E_DECL_INVALID", "scope must be a non-empty string")
        parsed["scope"] = scope.strip()
    if "normalization" in overrides:
        parsed["normalization"] = _policy(overrides)
    if "tables" in overrides:
        parsed["tables"] = validate_tables(overrides["tables"])
    if "priority" in overrides:
        parsed["priority"] = _string_list(overrides["priority"], "priority")
    if "merge_targets" in overrides:
        parsed["merge_targets"] = _string_list(
            overrides["merge_targets"], "merge_targets")
    return {"path": path, "name": _check_name(raw, path),
            "extends": (path.parent / extends).resolve(), "overrides": parsed}


def load_chain(path: Path) -> list[dict[str, Any]]:
    """Resolve the extends chain root-first, rejecting cycles deterministically."""

    current, chain, visiting = Path(path).resolve(), [], []
    while True:
        if current in visiting:
            start = visiting.index(current)
            fail("E_DECL_CYCLE",
                 "declaration inheritance chain contains a cycle",
                 cycle=[display_path(p) for p in visiting[start:]]
                 + [display_path(current)])
        if not current.is_file():
            fail("E_IO", "declaration file does not exist",
                 path=display_path(current))
        visiting.append(current)
        raw = read_yaml(current)
        if "extends" in raw:
            layer = child_layer(current)
            chain.append(layer)
            current = layer["extends"]
            continue
        if "overrides" in raw:
            fail("E_DECL_INVALID",
                 "overrides are only valid in a declaration that extends another",
                 path=display_path(current))
        chain.append(root_layer(current))
        break
    chain.reverse()
    return chain


def _origin(layers: list[dict[str, Any]], index: int,
            history: list[dict[str, Any]]) -> dict[str, Any]:
    return {"layer": index, "layer_name": layers[index]["name"],
            "overridden": [
                {"layer": item["layer"],
                 "layer_name": layers[item["layer"]]["name"],
                 "old_value": item["value"]} for item in history]}


def merge_chain(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold root-first layers using field-specific merge semantics."""

    origins: dict[str, int] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    state: dict[str, Any] = {}
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    table_origins: dict[str, dict[str, Any]] = {}
    merge_targets: list[str] | None = None
    priority: list[str] | None = None

    def replace(field: str, index: int, previous: Any) -> None:
        if field in origins:
            history.setdefault(field, []).append(
                {"layer": origins[field], "value": previous})
        origins[field] = index

    for index, layer in enumerate(layers):
        ov = layer if index == 0 else layer["overrides"]
        for field in ("scope", "normalization"):
            if index == 0 or field in ov:
                replace(field, index, state.get(field))
                state[field] = ov[field]
        for ref in ov.get("tables", []):
            table_id = ref["id"]
            if table_id in merged:
                origin = table_origins[table_id]
                origin["history"].append(
                    {"layer": origin["layer"], "value": dict(merged[table_id])})
                origin["layer"] = index
                merged[table_id].update(version=ref["version"],
                                        scopes=list(ref["scopes"]))
            else:
                merged[table_id] = dict(ref)
                order.append(table_id)
                table_origins[table_id] = {"layer": index, "history": []}
        if index == 0 or "priority" in ov:
            replace("priority", index,
                    list(priority) if priority is not None else None)
            priority = list(ov["priority"])
        if index == 0 or "merge_targets" in ov:
            value = ov["merge_targets"]
            if index == 0 and value is None:
                continue
            if merge_targets is None:
                merge_targets, origins["merge_targets"] = list(value), index
            else:
                history.setdefault("merge_targets", []).append(
                    {"layer": origins["merge_targets"], "value": list(merge_targets)})
                origins["merge_targets"] = index
                merge_targets += [t for t in value if t not in merge_targets]

    if priority is None:
        priority, origins["priority"] = list(order), 0
    leaf = layers[-1]
    resolved_chain = [
        {"layer": i, "name": layer["name"], "path": display_path(layer["path"]),
         "file_sha256": file_sha256(layer["path"]),
         "role": "root" if i == 0 else "child"}
        for i, layer in enumerate(layers)]
    field_origins = {
        field: _origin(layers, origins[field], history.get(field, []))
        for field in SCALAR_FIELDS if field in origins}
    field_origins["tables"] = {
        table_id: _origin(layers, table_origins[table_id]["layer"],
                          table_origins[table_id]["history"])
        for table_id in order}
    fingerprint = [{"path": item["path"], "file_sha256": item["file_sha256"]}
                   for item in resolved_chain]
    return {
        "name": leaf["name"], "scope": state["scope"],
        "normalization": state["normalization"],
        "tables": [merged[table_id] for table_id in order], "priority": priority,
        "merge_targets": merge_targets,
        "declaration_path": display_path(leaf["path"]),
        "policy": NormalizationPolicy.from_mapping(state["normalization"]),
        "resolved_chain": resolved_chain, "field_origins": field_origins,
        "chain_depth": len(layers), "declaration_hash": stable_hash(fingerprint)}
