"""Apply a compiled rule tree to local text and record token-level traces.

The text is either one file (the legacy path) or an ordered sequence of
named segments concatenated verbatim — no separator is inserted at the
joins, so a word split across a boundary stays one token.  Token offsets
are global char offsets into the concatenated text; segmented tokens also
carry the id of the segment they start in and a flag when they spill past
that segment's end.  ``result_hash`` commits only to the logical text (its
id and content hash), the rules and the normalization: segment provenance
is stored in the result but excluded from the hash, so re-segmenting the
same string under the same text id shares one snapshot.
"""

from __future__ import annotations

import hashlib
import unicodedata
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .clock import FixedClock
from .errors import fail
from .normalizer import NormalizationPolicy
from .store import LedgerStore
from .util import display_path, file_sha256, read_text, stable_hash

DEFAULT_SOURCE = {
    "table_id": None,
    "version": 0,
    "kind": "default",
    "line": 0,
    "rule": "default:keep",
}

# Token keys describing the segmentation, not the text; excluded from the
# result hash so re-segmenting the same string keeps the same hash.
SEGMENT_TOKEN_KEYS = frozenset({"segment_id", "crosses_boundary"})


def tokenize(text: str) -> list[dict[str, Any]]:
    """Split on whitespace and Unicode punctuation while keeping raw offsets."""

    tokens: list[dict[str, Any]] = []
    start: int | None = None
    for offset, char in enumerate(text):
        punctuation = unicodedata.category(char).startswith("P") and char != "_"
        separator = char.isspace() or punctuation
        if separator:
            if start is not None:
                tokens.append({"raw": text[start:offset], "start": start,
                               "end": offset})
                start = None
        elif start is None:
            start = offset
    if start is not None:
        tokens.append({"raw": text[start:], "start": start, "end": len(text)})
    return tokens


def tokenize_segments(
    text: str, ranges: list[tuple[str, int, int]]
) -> list[dict[str, Any]]:
    """Tokenize concatenated text and attribute each token to a segment.

    A token belongs to the segment containing its start offset; a token
    whose end spills past that segment's end is flagged as crossing a
    boundary.  No whitespace is synthesized at segment joins.
    """

    starts = [start for _, start, _ in ranges]
    tokens = []
    for token in tokenize(text):
        index = bisect_right(starts, token["start"]) - 1
        segment_id, _, segment_end = ranges[index]
        tokens.append(token | {"segment_id": segment_id,
                               "crosses_boundary": token["end"] > segment_end})
    return tokens


def _normalize_segments(segments: Iterable[Any]) -> list[tuple[str, Path]]:
    """Validate the segment sequence into a list of ``(id, path)`` pairs."""

    normalized: list[tuple[str, Path]] = []
    for item in segments:
        if isinstance(item, Mapping):
            item = item.get("id"), item.get("path")
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            fail("E_BAD_FORMAT", "each segment needs an id and a path",
                 segment=repr(item))
        segment_id, path = item
        if not isinstance(segment_id, str) or not segment_id:
            fail("E_BAD_FORMAT", "segment id must be a non-empty string",
                 segment_id=segment_id)
        if segment_id in {known for known, _ in normalized}:
            fail("E_BAD_FORMAT", "segment ids must be unique",
                 segment_id=segment_id)
        if not path:
            fail("E_BAD_FORMAT", "segment path is missing",
                 segment_id=segment_id)
        normalized.append((segment_id, Path(path)))
    if not normalized:
        fail("E_BAD_FORMAT", "segments must contain at least one segment")
    return normalized


def _read_segments(
    normalized: list[tuple[str, Path]],
) -> tuple[str, list[dict[str, Any]]]:
    """Concatenate segment contents and build the provenance entries."""

    parts, entries, offset = [], [], 0
    for segment_id, path in normalized:
        part = read_text(path)
        parts.append(part)
        entries.append({"id": segment_id, "source_path": display_path(path),
                        "sha256": file_sha256(path), "start": offset,
                        "end": offset + len(part)})
        offset += len(part)
    return "".join(parts), entries


def _canonical_semantic(semantic: dict[str, Any]) -> dict[str, Any]:
    """Strip segment provenance: the hash commits to the logical text only."""

    canonical = {k: v for k, v in semantic.items() if k != "segments"}
    canonical["tokens"] = [
        {k: v for k, v in token.items() if k not in SEGMENT_TOKEN_KEYS}
        for token in semantic["tokens"]
    ]
    return canonical


def _decision(rule: dict[str, Any] | None, raw: str) -> dict[str, Any]:
    if rule is None:
        return {
            "disposition": "keep",
            "target": None,
            "source": DEFAULT_SOURCE | {"raw": raw},
            "overridden_by": [],
        }
    return {
        "disposition": rule["disposition"],
        "target": rule["target"],
        "source": rule["source"],
        "overridden_by": rule.get("overridden_by", []),
    }


def build_snapshot(
    compilation: dict[str, Any],
    text_path: Path | None = None,
    scope: str | None = None,
    *,
    segments: Iterable[Any] | None = None,
    text_id: str | None = None,
) -> dict[str, Any]:
    if segments is not None and text_path is not None:
        fail("E_BAD_FORMAT", "pass either a text path or segments, not both")
    normalized: list[tuple[str, Path]] | None = None
    if segments is not None:
        normalized = _normalize_segments(segments)
        for segment_id, path in normalized:
            if not path.is_file():
                fail("E_IO", "segment file does not exist",
                     segment_id=segment_id, path=str(path))
    else:
        if text_path is None:
            fail("E_BAD_FORMAT", "a text path or segments are required")
        text_path = Path(text_path)
        if not text_path.is_file():
            fail("E_IO", "text fixture does not exist", path=str(text_path))
    effective_scope = scope or compilation["scope"]
    if effective_scope != compilation["scope"]:
        fail(
            "E_BAD_FORMAT",
            "snapshot scope must match the compiled scope",
            compilation_scope=compilation["scope"],
            requested_scope=effective_scope,
        )
    segment_entries: list[dict[str, Any]] | None = None
    if normalized is not None:
        text, segment_entries = _read_segments(normalized)
        ranges = [(e["id"], e["start"], e["end"]) for e in segment_entries]
        token_dicts = tokenize_segments(text, ranges)
        resolved_id = text_id if text_id is not None else "+".join(
            segment_id for segment_id, _ in normalized)
        # The content hash binds the concatenated string, never the files.
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        text = read_text(text_path)
        token_dicts = tokenize(text)
        resolved_id = text_id if text_id is not None else text_path.stem
        text_hash = file_sha256(text_path)
    if not resolved_id:
        fail("E_BAD_FORMAT", "text id must be a non-empty string")
    policy = NormalizationPolicy.from_mapping(compilation["normalization"])
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    token_rows: list[dict[str, Any]] = []
    counts = {"keep": 0, "drop": 0, "merge": 0, "whitelist_exempt": 0}
    for index, token in enumerate(token_dicts):
        term = policy.normalize_validated(token["raw"])
        decision = _decision(rules.get(term), token["raw"])
        counts[decision["disposition"]] += 1
        row = {
            "index": index,
            "raw": token["raw"],
            "start": token["start"],
            "end": token["end"],
            "term": term,
            "disposition": decision["disposition"],
            "target": decision["target"],
            "source": decision["source"],
            "overridden_by": decision["overridden_by"],
        }
        if segment_entries is not None:
            row["segment_id"] = token["segment_id"]
            row["crosses_boundary"] = token["crosses_boundary"]
        token_rows.append(row)
    summary = {
        "token_count": len(token_rows),
        **counts,
        "affected": len(token_rows) - counts["keep"],
    }
    semantic = {
        "kind": "snapshot",
        "schema_version": 1,
        "text_id": resolved_id,
        "text_hash": text_hash,
        "compilation": {
            "name": compilation["name"],
            "rules_hash": compilation["rules_hash"],
            "scope": compilation["scope"],
            "rule_count": compilation["rule_count"],
        },
        "scope": effective_scope,
        "normalization": compilation["normalization"],
        "tokens": token_rows,
        "summary": summary,
    }
    if segment_entries is not None:
        semantic["segments"] = segment_entries
    return semantic | {"result_hash": stable_hash(_canonical_semantic(semantic))}


def snapshot_text(
    store: LedgerStore,
    compilation_id: int,
    text_path: Path | None = None,
    clock: FixedClock | None = None,
    operator: str = "op-demo-01",
    scope: str | None = None,
    *,
    segments: Iterable[Any] | None = None,
    text_id: str | None = None,
) -> dict[str, Any]:
    compilation = store.get_compilation(compilation_id)
    if compilation is None:
        fail("E_REF_NOT_FOUND", "compilation does not exist",
             compilation_id=compilation_id)
    if clock is None:
        fail("E_BAD_FORMAT", "a clock is required")
    semantic = build_snapshot(compilation, text_path, scope,
                              segments=segments, text_id=text_id)
    if segment_entries := semantic.get("segments"):
        source_path = "+".join(e["source_path"] for e in segment_entries)
    else:
        source_path = display_path(Path(text_path))  # type: ignore[arg-type]
    payload = {
        "compilation_id": compilation_id,
        "text_id": semantic["text_id"],
        "source_path": source_path,
        "text_hash": semantic["text_hash"],
        "scope": semantic["scope"],
        "result_hash": semantic["result_hash"],
        "result": semantic,
    }
    saved = store.save_snapshot(payload, operator, clock.timestamp())
    store.add_audit(
        "snapshot.create",
        f"{compilation['name']}:{semantic['text_id']}:{semantic['result_hash'][:12]}",
        {"token_count": semantic["summary"]["token_count"],
         "affected": semantic["summary"]["affected"]},
        operator,
        clock.timestamp(),
    )
    return saved


def snapshot_many(
    store: LedgerStore,
    compilation_id: int,
    text_paths: Iterable[Path],
    clock: FixedClock,
    operator: str = "op-demo-01",
) -> list[dict[str, Any]]:
    paths = sorted((Path(path) for path in text_paths), key=lambda p: p.name)
    return [snapshot_text(store, compilation_id, path, clock, operator)
            for path in paths]
