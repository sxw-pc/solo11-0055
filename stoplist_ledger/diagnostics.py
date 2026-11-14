"""Pre-compilation scope diagnostics.

Runs before any table version is resolved, reusing load_declaration and
table_in_scope verbatim and never touching the store, so it cannot alter the
E_REF_NOT_FOUND / E_DUP_PRIORITY paths of build_compilation. Wildcards are
never expanded: declaration scope */all matches every table, and */all inside
a table's scopes makes that table visible under every declaration scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Union

from .compiler import load_declaration, table_in_scope

WILDCARDS = frozenset({"*", "all"})

SCOPE_WARNING_CODES: dict[str, str] = {
    "W_SCOPE_EXCLUDED": "table excluded: scopes and declaration scope do not "
                        "intersect",
    "W_SCOPE_NO_MATCH": "concrete declaration scope matches no referenced table",
    "W_SCOPE_EMPTY": "table scopes array was empty; load_declaration fell back "
                     "to ['*']",
    "W_SCOPE_UNKNOWN_LABEL": "table scope label cannot correspond to the "
                             "declaration scope or the */all wildcards",
}

DeclarationLike = Union[str, Path, Mapping[str, Any]]


def _ref_view(ref: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": ref["id"], "version": ref["version"],
            "scopes": list(ref["scopes"])}


def _warning(code: str, scope: str, ref: Mapping[str, Any] | None,
             **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code, "message": SCOPE_WARNING_CODES[code],
        "declaration_scope": scope,
        "table_id": ref["id"] if ref is not None else None,
        "ref_scopes": list(ref["scopes"]) if ref is not None else None,
    }
    payload.update(extra)
    return payload


def diagnose_scopes(declaration: DeclarationLike) -> dict[str, Any]:
    """Return active/excluded/empty-scope tables and stable-coded warnings.

    Accepts a declaration file path or a loaded declaration mapping. The table
    lists follow the reference order of the declaration.
    """

    loaded = dict(declaration) if isinstance(declaration, Mapping) \
        else load_declaration(Path(declaration))
    scope = loaded["scope"]
    refs = list(loaded["tables"])
    wildcard_scope = scope in WILDCARDS
    active_tables: list[dict[str, Any]] = []
    excluded_tables: list[dict[str, Any]] = []
    empty_scope_tables: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for ref in refs:
        if ref.get("_scopes_was_empty", False):
            empty_scope_tables.append(_ref_view(ref))
            # The ['*'] fallback keeps the table active; never double-report it
            # as an empty-array exclusion.
            warnings.append(_warning(
                "W_SCOPE_EMPTY", scope, ref, fallback=["*"],
                note="load_declaration replaces an empty scopes array with ['*']"))
        if table_in_scope(scope, ref["scopes"]):
            active_tables.append(_ref_view(ref))
        else:
            excluded_tables.append(_ref_view(ref))
            if not wildcard_scope:
                warnings.append(_warning("W_SCOPE_EXCLUDED", scope, ref))

    # A label is provably unknowable only when it is a non-wildcard label on an
    # excluded table and no active table carries it either: labels shared with
    # an active table correspond to another valid declaration scope.
    if not wildcard_scope:
        anchored = {scope}
        for ref in refs:
            if table_in_scope(scope, ref["scopes"]):
                anchored.update(s for s in ref["scopes"] if s not in WILDCARDS)
        for ref in refs:
            if table_in_scope(scope, ref["scopes"]):
                continue
            unknown = sorted(s for s in ref["scopes"]
                             if s not in WILDCARDS and s not in anchored)
            if unknown:
                warnings.append(_warning(
                    "W_SCOPE_UNKNOWN_LABEL", scope, ref, unknown_labels=unknown))

    if not wildcard_scope and not active_tables:
        warnings.append(_warning(
            "W_SCOPE_NO_MATCH", scope, None,
            tables=[_ref_view(ref) for ref in refs],
            note="declaration scope is a concrete value but no referenced "
                 "table is visible under it"))

    return {
        "kind": "scope_diagnosis",
        "schema_version": 1,
        "declaration_path": loaded.get("declaration_path"),
        "declaration_hash": loaded.get("declaration_hash"),
        "scope": scope,
        "active_tables": active_tables,
        "excluded_tables": excluded_tables,
        "empty_scope_tables": empty_scope_tables,
        "warnings": warnings,
    }


def diagnose_declaration_scopes(declaration_path: Path) -> dict[str, Any]:
    """Load a declaration file and run the pre-compilation scope diagnostics."""

    return diagnose_scopes(load_declaration(Path(declaration_path)))
