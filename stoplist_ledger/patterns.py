"""Wildcard pattern classification and matching for term-list entries.

A pattern is a normalized term carrying ``*`` wildcard markers. The wildcard
is accepted only at the leading edge, the trailing edge or both:

- ``pre*``   -> prefix match (one trailing wildcard),
- ``*suf``   -> suffix match (one leading wildcard),
- ``*mid*``  -> substring match (one wildcard on each edge).

A wildcard anywhere else, a bare ``*`` or any extra wildcard is rejected with
the stable ``E_BAD_PATTERN`` code. Synonym YAML entries never use wildcards;
a star there is treated as a literal (exact) term.
"""

from __future__ import annotations

from typing import Any

from .errors import fail

EXACT = "exact"
PREFIX = "prefix"
SUFFIX = "suffix"
SUBSTRING = "substring"
PATTERN_KINDS = (PREFIX, SUFFIX, SUBSTRING)
# Specificity ranking used when several rules match one token; lower wins.
SPECIFICITY_RANK = (EXACT, PREFIX, SUFFIX, SUBSTRING)
RANK = {kind: index for index, kind in enumerate(SPECIFICITY_RANK)}
_BAD_PATTERN = (
    "wildcard '*' is allowed only once at the start and/or end of a term "
    "(pre*, *suf or *mid*)"
)


def classify(term: str, raw: str | None = None,
             line: int | None = None) -> dict[str, Any]:
    """Classify a normalized term into an exact term or a wildcard pattern."""

    star_count = term.count("*")
    if star_count == 0:
        return {"pattern_kind": EXACT, "needle": None}
    # Enumerate the three legal shapes explicitly so any extra or interior
    # wildcard deterministically fails: pre*, *suf or *mid*.
    if star_count == 1 and term.endswith("*") and not term.startswith("*"):
        kind, needle = PREFIX, term[:-1]
    elif star_count == 1 and term.startswith("*") and not term.endswith("*"):
        kind, needle = SUFFIX, term[1:]
    elif (star_count == 2 and term.startswith("*") and term.endswith("*")
          and len(term) >= 3 and "*" not in term[1:-1]):
        kind, needle = SUBSTRING, term[1:-1]
    else:
        fail("E_BAD_PATTERN", _BAD_PATTERN,
             raw=raw if raw is not None else term, line=line)
    return {"pattern_kind": kind, "needle": needle}


def matches(pattern_kind: str, needle: str | None, term: str) -> bool:
    """Whether a normalized token ``term`` satisfies the given rule shape."""

    if pattern_kind == EXACT:
        return term == needle
    if pattern_kind == PREFIX:
        return term.startswith(needle)  # type: ignore[arg-type]
    if pattern_kind == SUFFIX:
        return term.endswith(needle)  # type: ignore[arg-type]
    return needle in term  # type: ignore[operator]


def build_index(rules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group pattern rules into the three searchable pattern buckets."""

    index: dict[str, list[dict[str, Any]]] = {kind: [] for kind in PATTERN_KINDS}
    for rule in rules:
        kind = rule.get("pattern_kind", EXACT)
        if kind != EXACT:
            index[kind].append(rule)
    for kind in PATTERN_KINDS:
        index[kind].sort(key=lambda rule: rule["term"])
    return index


def find_matches(term: str, exact_rules: dict[str, dict[str, Any]],
                 pattern_index: dict[str, list[dict[str, Any]]]
                 ) -> list[dict[str, Any]]:
    """Return every rule (exact and pattern) that matches a normalized token."""

    found: list[dict[str, Any]] = []
    exact = exact_rules.get(term)
    if exact is not None:
        found.append(exact)
    for kind in PATTERN_KINDS:
        found.extend(rule for rule in pattern_index.get(kind, ())
                     if matches(kind, rule["needle"], term))
    return found
