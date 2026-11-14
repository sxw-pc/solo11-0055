"""Small deterministic I/O and serialization helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import fail

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def canonical_json(value: Any) -> str:
    """Canonical bytes-ready JSON: sorted keys, no cosmetic whitespace."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("E_BAD_FORMAT", f"value is not JSON serializable: {exc}")

def pretty_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )

def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        fail("E_IO", f"cannot read {path}", reason=str(exc))
    return digest.hexdigest()

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        fail("E_IO", f"cannot read UTF-8 text from {path}", reason=str(exc))

def write_text(path: Path, text: str) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        fail("E_IO", f"cannot write {path}", reason=str(exc))
    return path

def display_path(path: Path) -> str:
    """A portable, non-machine-specific path for stable reports."""

    resolved = path.resolve()
    for base in (PROJECT_ROOT, Path.cwd().resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.name
