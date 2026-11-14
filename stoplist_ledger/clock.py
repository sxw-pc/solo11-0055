"""Injected clocks.

The project never calls datetime.now() in reporting paths.  A fixed default
timestamp makes demo artifacts byte-stable while still allowing a caller to
inject a different deterministic timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import fail

DEFAULT_NOW = "2024-01-01T00:00:00Z"

def validate_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value:
        fail("E_BAD_FORMAT", "clock value must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("E_BAD_FORMAT", "clock value is not valid ISO-8601", value=value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return value

@dataclass(frozen=True)
class FixedClock:
    """A deterministic clock suitable for tests and reproducible artifacts."""

    now: str = DEFAULT_NOW

    def __post_init__(self) -> None:
        validate_timestamp(self.now)

    def timestamp(self) -> str:
        return self.now

def make_clock(value: str | None = None) -> FixedClock:
    return FixedClock(validate_timestamp(value or DEFAULT_NOW))
