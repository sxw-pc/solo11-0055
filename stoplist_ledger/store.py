"""SQLite-backed local ledger for tables, compilations, snapshots and audits."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import fail
from .store_records import RecordMixin
from .util import canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS table_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL,
  source_path TEXT NOT NULL, policy_case TEXT NOT NULL,
  policy_whitespace TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
  line_count INTEGER NOT NULL, entry_count INTEGER NOT NULL,
  term_count INTEGER NOT NULL, comment_count INTEGER NOT NULL,
  entries_json TEXT NOT NULL, diff_json TEXT NOT NULL,
  operator TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(table_id, version)
);
CREATE TABLE IF NOT EXISTS compilations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, declaration_path TEXT NOT NULL,
  declaration_hash TEXT NOT NULL, scope TEXT NOT NULL,
  rules_hash TEXT NOT NULL, rules_json TEXT NOT NULL,
  operator TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(name, rules_hash)
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  compilation_id INTEGER NOT NULL, text_id TEXT NOT NULL,
  source_path TEXT NOT NULL, text_hash TEXT NOT NULL,
  scope TEXT NOT NULL, result_hash TEXT NOT NULL,
  result_json TEXT NOT NULL, operator TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(compilation_id, text_id, result_hash)
);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  declaration_path TEXT NOT NULL, declaration_hash TEXT NOT NULL,
  report_hash TEXT NOT NULL UNIQUE, report_json TEXT NOT NULL,
  operator TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  left_refs_json TEXT NOT NULL, right_refs_json TEXT NOT NULL,
  report_hash TEXT NOT NULL UNIQUE, report_json TEXT NOT NULL,
  operator TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL, subject TEXT NOT NULL, detail_json TEXT NOT NULL,
  operator TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class LedgerStore(RecordMixin):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> "LedgerStore":
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version','1')"
            )
        return self

    @staticmethod
    def _decode(row: sqlite3.Row, *fields: str) -> dict[str, Any]:
        record = dict(row)
        for field in fields:
            if field in record:
                record[field[:-5] if field.endswith("_json") else field] = json.loads(
                    record.pop(field)
                )
        return record

    # ---- table versions -------------------------------------------------
    def find_by_content(self, content_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM table_versions WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return self._decode(row, "entries_json", "diff_json") if row else None

    def get_version(self, table_id: str, version: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM table_versions WHERE table_id = ? AND version = ?",
                (table_id, version),
            ).fetchone()
        return self._decode(row, "entries_json", "diff_json") if row else None

    def get_version_by_id(self, version_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM table_versions WHERE id = ?", (version_id,)
            ).fetchone()
        return self._decode(row, "entries_json", "diff_json") if row else None

    def latest_version(self, table_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM table_versions WHERE table_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (table_id,),
            ).fetchone()
        return self._decode(row, "entries_json", "diff_json") if row else None

    def list_versions(self, table_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM table_versions"
        args: tuple[Any, ...] = ()
        if table_id is not None:
            sql += " WHERE table_id = ?"
            args = (table_id,)
        sql += " ORDER BY table_id, version"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._decode(row, "entries_json", "diff_json") for row in rows]

    def list_tables(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT table_id, MAX(version) AS latest_version, kind, "
                "COUNT(*) AS version_count FROM table_versions "
                "GROUP BY table_id, kind ORDER BY table_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def insert_version(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO table_versions(
                    table_id, version, kind, source_path, policy_case,
                    policy_whitespace, content_hash, line_count, entry_count,
                    term_count, comment_count, entries_json, diff_json,
                    operator, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["table_id"], record["version"], record["kind"],
                        record["source_path"], record["policy"]["case"],
                        record["policy"]["whitespace"], record["content_hash"],
                        record["line_count"], record["entry_count"],
                        record["term_count"], record["comment_count"],
                        canonical_json(record["entries"]),
                        canonical_json(record["diff"]), record["operator"],
                        record["created_at"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                fail("E_DUP_VERSION", "table version already exists", reason=str(exc))
            version_id = cursor.lastrowid
        return self.get_version_by_id(version_id)  # type: ignore[arg-type,return-value]

    # ---- compilations ---------------------------------------------------
    def save_compilation(self, payload: dict[str, Any], operator: str,
                         created_at: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compilations WHERE name = ? AND rules_hash = ?",
                (payload["name"], payload["rules_hash"]),
            ).fetchone()
            if row:
                return self._compilation(row)
            cursor = conn.execute(
                "INSERT INTO compilations(name, declaration_path, declaration_hash,"
                " scope, rules_hash, rules_json, operator, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (payload["name"], payload["declaration_path"],
                 payload["declaration_hash"], payload["scope"],
                 payload["rules_hash"], canonical_json(payload), operator, created_at),
            )
            record_id = cursor.lastrowid
        return self.get_compilation(record_id)  # type: ignore[arg-type,return-value]

    def _compilation(self, row: sqlite3.Row) -> dict[str, Any]:
        record = self._decode(row, "rules_json")
        payload = record.pop("rules")
        payload.update(record)
        payload["kind"] = "compilation"
        return payload

    def get_compilation(self, compilation_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compilations WHERE id = ?", (compilation_id,)
            ).fetchone()
        return self._compilation(row) if row else None

    def latest_compilation(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compilations WHERE name = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        return self._compilation(row) if row else None

    def list_compilations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM compilations ORDER BY id").fetchall()
        return [self._compilation(row) for row in rows]

    # ---- audit ----------------------------------------------------------
    def add_audit(self, event: str, subject: str, detail: dict[str, Any],
                  operator: str, created_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit(event, subject, detail_json, operator, created_at)"
                " VALUES(?,?,?,?,?)",
                (event, subject, canonical_json(detail), operator, created_at),
            )

    def list_audit(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
        return [self._decode(row, "detail_json") for row in rows]

    def summary(self) -> dict[str, int]:
        tables = ("table_versions", "compilations", "snapshots", "checks", "impacts")
        with self._connect() as conn:
            counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }
        return counts
