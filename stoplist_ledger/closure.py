"""Winning-proposal resolution and transitive merge-target closure.

The compiler and the checker both resolve tables and walk synonym chains
through this module so their priority, cycle, dropped-target and
missing-target verdicts always agree.
"""

from __future__ import annotations

from typing import Any, Iterable

from .normalizer import NormalizationPolicy

OK = "ok"
CYCLE = "cycle"
DROPPED = "dropped"
MISSING = "missing"
NOT_ALLOWED = "not_allowed"


def table_in_scope(scope: str, scopes: list[str]) -> bool:
    if scope in {"*", "all"}:
        return True
    return any(item in {"*", "all", scope} for item in scopes)


def make_proposal(source: dict[str, Any], disposition: str,
                  target: str | None = None) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "target": target,
        "source": source | {"rule": f"{source['kind']}:{disposition}"},
    }


def proposal_source(table: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "table_id": table["table_id"],
        "version": table["version"],
        "kind": table["kind"],
        "line": entry["line"],
        "raw": entry["raw"],
    }


def iter_candidates(
    declaration: dict[str, Any],
    versions: Iterable[dict[str, Any]],
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield (term, proposal) in priority-then-file order for active tables."""

    policy: NormalizationPolicy = declaration["policy"]
    active = {ref["id"] for ref in declaration["tables"]
              if table_in_scope(declaration["scope"], ref["scopes"])}
    by_id = {version["table_id"]: version for version in versions}
    for table_id in declaration["priority"]:
        if table_id not in active or table_id not in by_id:
            continue
        table = by_id[table_id]
        for entry in table["entries"]:
            term = policy.normalize_validated(entry["raw"])
            source = proposal_source(table, entry)
            if table["kind"] in {"stoplist", "noise"}:
                yield term, make_proposal(source, "drop")
            elif table["kind"] == "whitelist":
                yield term, make_proposal(source, "whitelist_exempt")
            else:
                target = policy.normalize_validated(entry["target_raw"])
                yield term, make_proposal(source, "merge", target)


def winning_proposals(
    declaration: dict[str, Any],
    versions: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """First-proposal-wins map in declared priority order (closure input)."""

    proposals: dict[str, dict[str, Any]] = {}
    for term, candidate in iter_candidates(declaration, versions):
        if term not in proposals:
            proposals[term] = candidate
    return proposals


def canonical_cycle(loop: list[str]) -> list[str]:
    """Close a loop of terms and rotate it to a stable, hashable order."""

    pivot = min(range(len(loop)), key=lambda index: loop[index])
    rotated = loop[pivot:] + loop[:pivot]
    return rotated + [rotated[0]]


def resolve_merge_chains(
    proposals: dict[str, dict[str, Any]],
    allowed: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Walk every winning merge proposal to its final endpoint.

    `proposals` maps a normalized term to its winning proposal
    (`disposition`/`target`/`source`); `allowed` is the normalized
    declaration allow-list, or None when no allow-list was declared.

    Returns one resolution per merge term with `status` in:
      - `ok`:          chain lands on a kept/allow-listed endpoint
      - `cycle`:       chain loops back on itself (`cycle` holds the loop)
      - `dropped`:     endpoint is dropped by a higher-priority table
      - `missing`:     endpoint is absent from all tables and no allow-list
                       vouches for it
      - `not_allowed`: endpoint is outside the declared allow-list

    Every resolution carries the walked `chain`: ordered
    {"term", "target", "source"} steps naming the table behind each hop.
    """

    resolutions: dict[str, dict[str, Any]] = {}
    for term in sorted(proposals):
        if proposals[term]["disposition"] != "merge":
            continue
        chain: list[dict[str, Any]] = []
        path = [term]
        current = term
        while True:
            target = proposals[current]["target"]
            chain.append({"term": current, "target": target,
                          "source": proposals[current]["source"]})
            if target in path:
                resolutions[term] = {
                    "status": CYCLE,
                    "cycle": canonical_cycle(path[path.index(target):]),
                    "chain": chain,
                }
                break
            winning = proposals.get(target)
            if winning is None:
                if allowed is None:
                    status = MISSING
                else:
                    status = OK if target in allowed else NOT_ALLOWED
                resolutions[term] = _endpoint(status, target, chain)
                break
            if winning["disposition"] == "merge":
                path.append(target)
                current = target
                continue
            if winning["disposition"] == "drop":
                resolutions[term] = _endpoint(
                    DROPPED, target, chain, dropped_by=winning["source"]
                )
            elif allowed is not None and target not in allowed:
                resolutions[term] = _endpoint(NOT_ALLOWED, target, chain)
            else:
                resolutions[term] = _endpoint(OK, target, chain)
            break
    return resolutions


def _endpoint(status: str, target: str, chain: list[dict[str, Any]],
              **extra: Any) -> dict[str, Any]:
    return {"status": status, "endpoint": target, "chain": chain, **extra}
