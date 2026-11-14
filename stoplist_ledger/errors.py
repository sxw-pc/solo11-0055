"""Stable error codes used by every stoplist-ledger surface."""

from __future__ import annotations

from typing import Any

ERROR_CODES: dict[str, str] = {
    "E_EMPTY_TERM": "A term or synonym target is empty after normalization",
    "E_CTRL_CHAR": "A term contains a control or format character",
    "E_TERM_TOO_LONG": "A normalized term exceeds 256 characters",
    "E_BAD_FORMAT": "The input file or declaration does not match its contract",
    "E_BAD_POLICY": "The normalization policy is unknown or malformed",
    "E_DUP_CONTENT": "The same normalized table content is already registered",
    "E_DUP_VERSION": "The table id and version already exist with different content",
    "E_DUP_PRIORITY": "A priority list contains a duplicate table id",
    "E_REF_NOT_FOUND": "A referenced table, version, compilation or snapshot is missing",
    "E_LINEAGE_GAP": "A table version chain has gaps and cannot be replayed",
    "E_MERGE_TARGET_NOT_ALLOWED": "A merge target is outside the declared allow-list",
    "E_DECL_INVALID": "The declaration is structurally invalid",
    "E_CONFIRM_REQUIRED": "A destructive reset requires explicit confirmation",
    "E_UNSAFE_RESET": "A reset target is outside the default var directory",
    "E_NOT_LOCAL": "The audit server may bind only to a loopback address",
    "E_IO": "A local file operation failed",
}


class LedgerError(Exception):
    """An expected, user-facing failure with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def fail(code: str, message: str, **details: Any) -> None:
    """Raise a LedgerError; kept as a function for compact validation branches."""

    raise LedgerError(code, message, details)
