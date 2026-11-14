"""Apply a compiled rule tree to local text and record token-level traces."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Iterable

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


def build_snapshot(compilation: dict[str, Any], text_path: Path,
                   scope: str | None = None) -> dict[str, Any]:
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
    text = read_text(text_path)
    policy = NormalizationPolicy.from_mapping(compilation["normalization"])
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    token_rows: list[dict[str, Any]] = []
    counts = {"keep": 0, "drop": 0, "merge": 0, "whitelist_exempt": 0}
    for index, token in enumerate(tokenize(text)):
        term = policy.normalize_validated(token["raw"])
        decision = _decision(rules.get(term), token["raw"])
        counts[decision["disposition"]] += 1
        token_rows.append(
            {
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
        )
    summary = {
        "token_count": len(token_rows),
        **counts,
        "affected": len(token_rows) - counts["keep"],
    }
    semantic = {
        "kind": "snapshot",
        "schema_version": 1,
        "text_id": text_path.stem,
        "text_hash": file_sha256(text_path),
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
    return semantic | {"result_hash": stable_hash(semantic)}


def snapshot_text(
    store: LedgerStore,
    compilation_id: int,
    text_path: Path,
    clock: FixedClock,
    operator: str = "op-demo-01",
    scope: str | None = None,
) -> dict[str, Any]:
    compilation = store.get_compilation(compilation_id)
    if compilation is None:
        fail("E_REF_NOT_FOUND", "compilation does not exist",
             compilation_id=compilation_id)
    semantic = build_snapshot(compilation, text_path, scope)
    payload = {
        "compilation_id": compilation_id,
        "text_id": semantic["text_id"],
        "source_path": display_path(Path(text_path)),
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
