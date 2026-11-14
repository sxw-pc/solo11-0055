"""Reusable, process-local rule lookup index used by snapshots."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

from .util import stable_hash

LOOKUP_STATS_TOKEN_THRESHOLD = 64
_CACHE_LIMIT = 8
_CACHE: "OrderedDict[str, RuleIndex]" = OrderedDict()


class RuleIndex:
    """Direct term hits plus prefix candidates bucketed by normalized length."""

    def __init__(self, rules: Iterable[dict[str, Any]]) -> None:
        self.by_term: dict[str, dict[str, Any]] = {}
        self.buckets: dict[int, dict[str, dict[str, Any]]] = {}
        for rule in rules:  # later entries win, matching the linear dict build
            term = rule["term"]
            self.by_term[term] = rule
            self.buckets.setdefault(len(term), {})[term] = rule

    def lookup(self, term: str) -> dict[str, Any] | None:
        return self.by_term.get(term)

    def prefix_candidates(self, prefix: str) -> list[str]:
        """Bucket by length first, then keep terms that extend the prefix."""

        return sorted(
            term
            for length, bucket in self.buckets.items()
            if length >= len(prefix)
            for term in bucket
            if term.startswith(prefix)
        )

    def bucket_size(self, term: str) -> int:
        return len(self.buckets.get(len(term), {}))

    @property
    def term_count(self) -> int:
        return len(self.by_term)

    @property
    def bucket_count(self) -> int:
        return len(self.buckets)


def _cache_key(compilation: dict[str, Any]) -> str:
    rules_hash = compilation.get("rules_hash")
    if isinstance(rules_hash, str) and rules_hash:
        return rules_hash
    return stable_hash({"scope": compilation.get("scope"),
                        "normalization": compilation.get("normalization"),
                        "rules": compilation.get("rules", [])})


def get_rule_index(compilation: dict[str, Any]) -> tuple[RuleIndex, bool]:
    """Return ``(index, reused)`` for this compilation's rule tree."""

    key = _cache_key(compilation)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached, True
    index = RuleIndex(compilation.get("rules", []))
    _CACHE[key] = index
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
    return index, False


def clear_rule_index_cache() -> None:
    _CACHE.clear()
