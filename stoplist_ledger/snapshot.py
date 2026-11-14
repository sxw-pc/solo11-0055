"""Apply a compiled rule tree to local text and record token-level traces."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .clock import FixedClock
from .errors import fail
from .normalizer import NormalizationPolicy
from .patterns import EXACT, PATTERN_KINDS, RANK, build_index, find_matches
from .store import LedgerStore
from .util import display_path, file_sha256, read_text, stable_hash

DEFAULT_SOURCE = {
    "table_id": None,
    "version": 0,
    "kind": "default",
    "line": 0,
    "rule": "default:keep",
}

# Reason text for candidates that lose the per-token arbitration.
LOSER_REASONS = {
    "exact_wins": "lower specificity than the winning exact rule",
    "specificity": "lower pattern specificity than the winning rule",
    "priority": "lower declared priority than the winning rule",
    "pattern_order": "later pattern term order than the winning rule",
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


def _arbitrate(
    candidates: list[dict[str, Any]], priority_rank: dict[str, int]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pick the unique winning rule for one token.

    Specificity sequence (high to low): exact, prefix, suffix, substring.
    Ties fall back to the declaration priority of the rule's source table and
    then to the ascending pattern term, so the result never depends on dict or
    insertion order.
    """

    def sort_key(rule: dict[str, Any]) -> tuple[Any, ...]:
        kind = rule.get("pattern_kind", EXACT)
        return (
            RANK[kind],
            priority_rank.get(rule["source"]["table_id"], len(priority_rank)),
            rule["term"],
        )

    ordered = sorted(candidates, key=sort_key)
    winner, winner_key = ordered[0], sort_key(ordered[0])
    losers: list[dict[str, Any]] = []
    for rule in ordered[1:]:
        key = sort_key(rule)
        if RANK[rule.get("pattern_kind", EXACT)] != winner_key[0]:
            reason = (LOSER_REASONS["exact_wins"]
                      if winner.get("pattern_kind", EXACT) == EXACT
                      else LOSER_REASONS["specificity"])
        elif key[1] != winner_key[1]:
            reason = LOSER_REASONS["priority"]
        else:
            reason = LOSER_REASONS["pattern_order"]
        losers.append({
            "disposition": rule["disposition"], "target": rule["target"],
            "source": rule["source"], "reason": reason,
            "match_type": rule.get("pattern_kind", EXACT),
            "term": rule["term"],
        })
    return winner, losers


def _decision(rule: dict[str, Any] | None, raw: str,
              match_type: str = EXACT,
              runtime_losers: list[dict[str, Any]] | None = None,
              include_pattern_trace: bool = False) -> dict[str, Any]:
    if rule is None:
        decision = {
            "disposition": "keep",
            "target": None,
            "source": DEFAULT_SOURCE | {"raw": raw},
            "overridden_by": [],
        }
        if include_pattern_trace:
            decision["match_type"] = match_type
        return decision
    overridden_by = list(rule.get("overridden_by", []))
    if runtime_losers:
        overridden_by = overridden_by + runtime_losers
    decision = {
        "disposition": rule["disposition"],
        "target": rule["target"],
        "source": rule["source"],
        "overridden_by": overridden_by,
    }
    if include_pattern_trace:
        decision["match_type"] = match_type
    return decision


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
    all_rules = compilation["rules"]
    exact_rules = {
        rule["term"]: rule for rule in all_rules
        if rule.get("pattern_kind", EXACT) == EXACT
    }
    stored_index = compilation.get("pattern_index")
    pattern_index = (
        {kind: list(stored_index.get(kind, [])) for kind in PATTERN_KINDS}
        if stored_index is not None else build_index(all_rules)
    )
    has_patterns = any(pattern_index[kind] for kind in PATTERN_KINDS)
    priority_rank = {
        table_id: index
        for index, table_id in enumerate(compilation["priority"])
    }
    token_rows: list[dict[str, Any]] = []
    counts = {"keep": 0, "drop": 0, "merge": 0, "whitelist_exempt": 0}
    for index, token in enumerate(tokenize(text)):
        term = policy.normalize_validated(token["raw"])
        candidates = find_matches(term, exact_rules, pattern_index)
        if not candidates:
            decision = _decision(
                None, token["raw"],
                include_pattern_trace=has_patterns,
            )
        else:
            winner, runtime_losers = _arbitrate(candidates, priority_rank)
            match_type = winner.get("pattern_kind", EXACT)
            decision = _decision(
                winner, token["raw"], match_type=match_type,
                runtime_losers=runtime_losers if has_patterns else None,
                include_pattern_trace=has_patterns,
            )
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
        if "match_type" in decision:
            row["match_type"] = decision["match_type"]
        token_rows.append(row)
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
