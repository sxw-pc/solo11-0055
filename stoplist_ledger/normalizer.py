"""Explicit, idempotent term normalization policies."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import fail

CASE_POLICIES = ("case_sensitive", "lower", "nfkc_lower")
WHITESPACE_POLICIES = ("trim", "collapse")
MAX_TERM_LENGTH = 256
_WS = re.compile(r"\s+", flags=re.UNICODE)

@dataclass(frozen=True)
class NormalizationPolicy:
    case: str = "nfkc_lower"
    whitespace: str = "collapse"

    def __post_init__(self) -> None:
        if self.case not in CASE_POLICIES:
            fail("E_BAD_POLICY", "unknown case policy", case=self.case,
                 allowed=list(CASE_POLICIES))
        if self.whitespace not in WHITESPACE_POLICIES:
            fail("E_BAD_POLICY", "unknown whitespace policy",
                 whitespace=self.whitespace, allowed=list(WHITESPACE_POLICIES))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "NormalizationPolicy":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            fail("E_BAD_POLICY", "normalization must be a mapping")
        unknown = set(value) - {"case", "whitespace"}
        if unknown:
            fail("E_BAD_POLICY", "unknown normalization keys", keys=sorted(unknown))
        return cls(
            case=str(value.get("case", "nfkc_lower")),
            whitespace=str(value.get("whitespace", "collapse")),
        )

    def to_dict(self) -> dict[str, str]:
        return {"case": self.case, "whitespace": self.whitespace}

    def normalize(self, term: str) -> str:
        """Apply the policy without validation (used only after validation)."""

        if not isinstance(term, str):
            fail("E_BAD_FORMAT", "term must be a string")
        if self.case == "lower":
            term = term.lower()
        elif self.case == "nfkc_lower":
            term = unicodedata.normalize("NFKC", term).lower()
        if self.whitespace == "trim":
            term = term.strip()
        else:
            term = _WS.sub(" ", term).strip()
        return term

    def normalize_validated(self, term: str) -> str:
        """Normalize and enforce the shared term contract."""

        normalized = self.normalize(term)
        if not normalized:
            fail("E_EMPTY_TERM", "term is empty after normalization", term=term)
        if len(normalized) > MAX_TERM_LENGTH:
            fail("E_TERM_TOO_LONG", "normalized term exceeds the length limit",
                 length=len(normalized), limit=MAX_TERM_LENGTH)
        for char in normalized:
            if unicodedata.category(char) in {"Cc", "Cf"}:
                fail("E_CTRL_CHAR",
                     "term contains a control or format character",
                     codepoint=f"U+{ord(char):04X}")
        return normalized

    def validate_mapping_entry(self, source: str, target: str) -> tuple[str, str]:
        return (self.normalize_validated(source),
                self.normalize_validated(target))

def assert_idempotent(policy: NormalizationPolicy, term: str) -> str:
    once = policy.normalize_validated(term)
    twice = policy.normalize_validated(once)
    if once != twice:
        fail("E_BAD_POLICY", "normalization is not idempotent", term=term)
    return once
