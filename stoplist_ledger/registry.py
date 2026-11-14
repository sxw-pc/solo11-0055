"""Table file parsing, version inference and registration."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .clock import FixedClock
from .errors import fail
from .normalizer import NormalizationPolicy
from .store import LedgerStore
from .util import display_path, read_text, stable_hash

KINDS = ("stoplist", "synonym", "noise", "whitelist")
_ID_RE = re.compile(r"^(?P<table>[A-Za-z0-9][A-Za-z0-9_-]*)_v(?P<version>[1-9]\d*)$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class ParsedTable:
    kind: str
    entries: list[dict[str, Any]]
    line_count: int
    comment_count: int
    term_count: int


def infer_kind(path: Path, explicit: str | None = None) -> str:
    suffix = path.suffix.lower()
    if explicit is not None:
        if explicit not in KINDS:
            fail("E_BAD_FORMAT", "unknown table kind", kind=explicit, allowed=list(KINDS))
        kind = explicit
    elif suffix in {".yaml", ".yml"}:
        kind = "synonym"
    else:
        lowered = path.stem.lower()
        if "whitelist" in lowered:
            kind = "whitelist"
        elif "noise" in lowered:
            kind = "noise"
        elif "synonym" in lowered:
            kind = "synonym"
        else:
            kind = "stoplist"
    if kind == "synonym" and suffix not in {".yaml", ".yml"}:
        fail("E_BAD_FORMAT", "synonym tables must be YAML mappings", path=path.name)
    if suffix in {".yaml", ".yml"} and kind != "synonym":
        fail("E_BAD_FORMAT", "YAML tables are reserved for synonym mappings", path=path.name)
    return kind


def parse_table(path: Path, kind: str,
                policy: NormalizationPolicy) -> ParsedTable:
    text = read_text(path)
    lines = text.splitlines()
    comment_count = sum(1 for line in lines if line.strip().startswith("#"))
    if kind == "synonym":
        entries = _parse_synonym_yaml(text, policy, path)
    else:
        entries = _parse_txt_terms(lines, policy)
    terms = {entry["term"] for entry in entries}
    return ParsedTable(
        kind=kind,
        entries=entries,
        line_count=len(lines),
        comment_count=comment_count,
        term_count=len(terms),
    )


def _parse_txt_terms(lines: list[str],
                     policy: NormalizationPolicy) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        term = policy.normalize_validated(raw)
        entries.append({"line": line_no, "raw": raw, "term": term})
    return entries


def _parse_synonym_yaml(text: str, policy: NormalizationPolicy,
                        path: Path) -> list[dict[str, Any]]:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        fail("E_BAD_FORMAT", f"invalid YAML in {path.name}", reason=str(exc))
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        fail("E_BAD_FORMAT", "synonym YAML must be a mapping of term to target")
    entries: list[dict[str, Any]] = []
    for logical_line, (source, target) in enumerate(loaded.items(), start=1):
        if not isinstance(source, str) or not isinstance(target, str):
            fail(
                "E_BAD_FORMAT",
                "synonym keys and targets must be strings",
                line=logical_line,
                source_type=type(source).__name__,
                target_type=type(target).__name__,
            )
        term, normalized_target = policy.validate_mapping_entry(source, target)
        entries.append(
            {
                "line": logical_line,
                "raw": source,
                "term": term,
                "target_raw": target,
                "target": normalized_target,
            }
        )
    return entries


def _signatures(entries: list[dict[str, Any]]) -> list[str]:
    result = []
    for entry in entries:
        if "target_raw" in entry:
            result.append(f"{entry['raw']} => {entry['target_raw']}")
        else:
            result.append(entry["raw"])
    return result


def diff_entries(previous: list[dict[str, Any]] | None,
                 current: list[dict[str, Any]]) -> dict[str, Any]:
    if previous is None:
        return {
            "base_version": None,
            "added": [entry["line"] for entry in current],
            "removed": [],
            "modified": [],
            "added_count": len(current),
            "removed_count": 0,
            "modified_count": 0,
        }
    old_sigs, new_sigs = _signatures(previous), _signatures(current)
    matcher = difflib.SequenceMatcher(None, old_sigs, new_sigs, autojunk=False)
    added: list[int] = []
    removed: list[int] = []
    modified: list[dict[str, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            pairs = min(i2 - i1, j2 - j1)
            for offset in range(pairs):
                modified.append(
                    {"old": previous[i1 + offset]["line"],
                     "new": current[j1 + offset]["line"]}
                )
            removed.extend(entry["line"] for entry in previous[i1 + pairs:i2])
            added.extend(entry["line"] for entry in current[j1 + pairs:j2])
        elif tag == "delete":
            removed.extend(entry["line"] for entry in previous[i1:i2])
        else:
            added.extend(entry["line"] for entry in current[j1:j2])
    return {
        "base_version": previous[0].get("_version") if previous else None,
        "added": added,
        "removed": removed,
        "modified": modified,
        "added_count": len(added),
        "removed_count": len(removed),
        "modified_count": len(modified),
    }


def _content_hash(kind: str, entries: list[dict[str, Any]]) -> str:
    if kind == "synonym":
        payload = {
            "kind": kind,
            "map": sorted([entry["term"], entry["target"]] for entry in entries),
        }
    else:
        payload = {"kind": kind, "terms": [entry["term"] for entry in entries]}
    return stable_hash(payload)


def infer_identity(path: Path, table_id: str | None,
                   version: int | None) -> tuple[str, int | None]:
    match = _ID_RE.match(path.stem)
    inferred_table = match.group("table") if match else None
    inferred_version = int(match.group("version")) if match else None
    resolved_table = table_id or inferred_table
    if not resolved_table or not _TABLE_RE.fullmatch(resolved_table):
        fail(
            "E_BAD_FORMAT",
            "table id is missing or invalid; use <id>_v<version>.<ext>",
            filename=path.name,
        )
    if version is not None and (not isinstance(version, int) or version < 1):
        fail("E_BAD_FORMAT", "version must be a positive integer", version=version)
    return resolved_table, version if version is not None else inferred_version


def register_file(
    store: LedgerStore,
    path: Path,
    clock: FixedClock,
    operator: str = "op-demo-01",
    kind: str | None = None,
    table_id: str | None = None,
    version: int | None = None,
    policy: NormalizationPolicy | None = None,
    allow_existing: bool = False,
) -> dict[str, Any]:
    """Register one local table file; content duplicates are rejected by default."""

    path = Path(path)
    if not path.is_file():
        fail("E_IO", "table file does not exist", path=str(path))
    resolved_kind = infer_kind(path, kind)
    resolved_policy = policy or NormalizationPolicy()
    parsed = parse_table(path, resolved_kind, resolved_policy)
    resolved_table, requested_version = infer_identity(path, table_id, version)
    if requested_version is None:
        latest = store.latest_version(resolved_table)
        requested_version = (latest["version"] + 1) if latest else 1
    content_hash = _content_hash(resolved_kind, parsed.entries)

    existing_content = store.find_by_content(content_hash)
    if existing_content:
        same_slot = (
            existing_content["table_id"] == resolved_table
            and existing_content["version"] == requested_version
        )
        if allow_existing and same_slot:
            return existing_content
        fail(
            "E_DUP_CONTENT",
            "identical normalized content is already registered",
            existing_table=existing_content["table_id"],
            existing_version=existing_content["version"],
            content_hash=content_hash,
        )
    if store.get_version(resolved_table, requested_version):
        fail(
            "E_DUP_VERSION",
            "table version exists with different content",
            table_id=resolved_table,
            version=requested_version,
        )

    latest = store.latest_version(resolved_table)
    diff = diff_entries(latest["entries"] if latest else None, parsed.entries)
    diff["base_version"] = latest["version"] if latest else None
    record = {
        "table_id": resolved_table,
        "version": requested_version,
        "kind": resolved_kind,
        "source_path": display_path(path),
        "policy": resolved_policy.to_dict(),
        "content_hash": content_hash,
        "line_count": parsed.line_count,
        "entry_count": len(parsed.entries),
        "term_count": parsed.term_count,
        "comment_count": parsed.comment_count,
        "entries": parsed.entries,
        "diff": diff,
        "operator": operator,
        "created_at": clock.timestamp(),
    }
    saved = store.insert_version(record)
    store.add_audit(
        "table.register",
        f"{resolved_table}@v{requested_version}",
        {"content_hash": content_hash, "kind": resolved_kind,
         "entry_count": len(parsed.entries)},
        operator,
        clock.timestamp(),
    )
    return saved
