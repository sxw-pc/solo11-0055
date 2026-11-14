"""Version lineage reconstruction by replaying entry-level changes."""

from __future__ import annotations

from typing import Any, Callable

from .errors import fail
from .registry import entry_identity
from .store import LedgerStore


def _view(entry: dict[str, Any]) -> dict[str, Any]:
    view = {"line": entry["line"], "raw": entry["raw"], "term": entry["term"]}
    if "target" in entry:
        view["target"] = entry["target"]
    return view


def fold_entry_changes(previous: list[dict[str, Any]],
                       current: list[dict[str, Any]],
                       stored_diff: dict[str, Any] | None = None
                       ) -> dict[str, Any]:
    """Fold two entry lists into index-based add/remove/modify changes.

    Identical entries are consumed as a multiset first, so reordered
    duplicate lines never surface as modifications.  Leftovers pair up as
    retargets (same term), renames (same target) or via the line pairs
    hinted by the stored difflib diff; the rest are removals/additions.
    """
    buckets: dict[tuple[str, str | None], list[int]] = {}
    for index, entry in enumerate(current):
        buckets.setdefault(entry_identity(entry), []).append(index)
    unchanged: list[tuple[int, int]] = []
    old_rest: list[int] = []
    for index, entry in enumerate(previous):
        bucket = buckets.get(entry_identity(entry))
        if bucket:
            unchanged.append((index, bucket.pop(0)))
        else:
            old_rest.append(index)
    new_rest = [index for bucket in buckets.values() for index in bucket]
    modified: list[tuple[int, int, str]] = []

    def link(old_index: int, new_index: int) -> None:
        old, new = previous[old_index], current[new_index]
        renamed = old["term"] != new["term"]
        retargeted = old.get("target") != new.get("target")
        kind = "modified" if renamed and retargeted else (
            "renamed" if renamed else "retargeted")
        modified.append((old_index, new_index, kind))
        old_rest.remove(old_index)
        new_rest.remove(new_index)

    def sweep(match: Callable[[dict[str, Any], dict[str, Any]], bool]) -> None:
        for old_index in list(old_rest):
            hit = next((i for i in new_rest
                        if match(previous[old_index], current[i])), None)
            if hit is not None:
                link(old_index, hit)

    sweep(lambda o, n: "target" in o and "target" in n
          and o["term"] == n["term"])
    sweep(lambda o, n: o.get("target") is not None
          and o.get("target") == n.get("target"))
    for hint in (stored_diff or {}).get("modified", []):
        old_index = next((i for i in old_rest
                          if previous[i]["line"] == hint.get("old")), None)
        new_index = next((i for i in new_rest
                          if current[i]["line"] == hint.get("new")), None)
        if old_index is not None and new_index is not None:
            link(old_index, new_index)
    return {
        "unchanged": sorted(unchanged),
        "modified": sorted(modified),
        "removed": sorted(old_rest),
        "added": sorted(new_rest),
    }


def reconstruct_lineage(store: LedgerStore, table_id: str,
                        version: int | None = None) -> dict[str, Any]:
    """Rebuild the entry set of table_id@version with per-entry lineage.

    Replays entry-level changes from the table's first registered version.
    A gap in the version chain aborts with E_LINEAGE_GAP instead of
    silently substituting another version.
    """
    chain = store.version_chain(table_id)
    if not chain:
        fail("E_REF_NOT_FOUND", "table is not registered", table_id=table_id)
    if version is None:
        version = chain[-1]["version"]
    chain = [record for record in chain if record["version"] <= version]
    if not chain or chain[-1]["version"] != version:
        fail("E_REF_NOT_FOUND", "table version is not registered",
             table_id=table_id, version=version)
    numbers = [record["version"] for record in chain]
    missing = [n for n in range(numbers[0], version + 1) if n not in numbers]
    if missing:
        fail("E_LINEAGE_GAP", "version chain is incomplete; cannot rebuild",
             table_id=table_id, version=version, missing_versions=missing,
             registered_versions=numbers)
    nodes = [
        {"introduced": chain[0]["version"], "history": [
            {"version": chain[0]["version"], "change": "added",
             "entry": _view(entry)}]}
        for entry in chain[0]["entries"]
    ]
    for prev, curr in zip(chain, chain[1:]):
        diff = curr["diff"]
        hint = diff if diff.get("base_version") == prev["version"] else None
        changes = fold_entry_changes(prev["entries"], curr["entries"], hint)
        moved: dict[int, dict[str, Any]] = {}
        for old_index, new_index in changes["unchanged"]:
            moved[new_index] = nodes[old_index]
        for old_index, new_index, kind in changes["modified"]:
            node = nodes[old_index]
            node["history"].append({
                "version": curr["version"], "change": kind,
                "from": _view(prev["entries"][old_index]),
                "to": _view(curr["entries"][new_index]),
            })
            moved[new_index] = node
        nodes = []
        for new_index, entry in enumerate(curr["entries"]):
            nodes.append(moved.get(new_index) or {
                "introduced": curr["version"],
                "history": [{"version": curr["version"], "change": "added",
                             "entry": _view(entry)}],
            })
    entries = []
    for entry, node in zip(chain[-1]["entries"], nodes):
        item = {
            "line": entry["line"], "raw": entry["raw"], "term": entry["term"],
            "introduced_version": node["introduced"],
            "modified_count": len(node["history"]) - 1,
            "history": node["history"],
        }
        if "target" in entry:
            item["target"] = entry["target"]
        entries.append(item)
    return {
        "kind": "lineage",
        "table_id": table_id,
        "table_kind": chain[-1]["kind"],
        "version": version,
        "first_version": numbers[0],
        "versions_replayed": numbers,
        "entry_count": len(entries),
        "entries": entries,
    }
