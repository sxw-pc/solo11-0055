"""Ledger mixin for snapshots, check reports and impact reports."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .util import canonical_json

class RecordMixin:
    """Methods that assume the host class provides _connect and _decode."""

    def _connect(self):  # pragma: no cover - narrowed by the host class
        raise NotImplementedError

    def _decode(self, row: sqlite3.Row, *fields: str) -> dict[str, Any]:
        raise NotImplementedError

    def _fetch_one(self, table: str, row_id: int, loader):
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (row_id,)
            ).fetchone()
        return loader(row) if row else None

    def _fetch_all(self, table: str, loader, where: str = "",
                   args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [loader(row) for row in rows]

    # ---- snapshots ------------------------------------------------------
    def save_snapshot(self, payload: dict[str, Any], operator: str,
                      created_at: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE compilation_id = ? AND text_id = ?"
                " AND result_hash = ?",
                (payload["compilation_id"], payload["text_id"],
                 payload["result_hash"]),
            ).fetchone()
            if row:
                return self._snapshot(row)
            cursor = conn.execute(
                "INSERT INTO snapshots(compilation_id, text_id, source_path,"
                " text_hash, scope, result_hash, result_json, operator, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (payload["compilation_id"], payload["text_id"],
                 payload["source_path"], payload["text_hash"], payload["scope"],
                 payload["result_hash"], canonical_json(payload), operator,
                 created_at),
            )
            snap_id = cursor.lastrowid
        return self.get_snapshot(snap_id)  # type: ignore[arg-type,return-value]

    def _snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        stored = json.loads(row["result_json"])
        semantic = stored.pop("result")
        stored.update(semantic)
        stored.update({"id": row["id"], "operator": row["operator"],
                       "created_at": row["created_at"], "kind": "snapshot"})
        return stored

    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        return self._fetch_one("snapshots", snapshot_id, self._snapshot)

    def list_snapshots(self, compilation_id: int | None = None) -> list[dict[str, Any]]:
        if compilation_id is None:
            return self._fetch_all("snapshots", self._snapshot)
        return self._fetch_all(
            "snapshots", self._snapshot, "compilation_id = ?", (compilation_id,)
        )

    # ---- checks ---------------------------------------------------------
    def save_check(self, declaration_path: str, declaration_hash: str,
                   report: dict[str, Any], operator: str,
                   created_at: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM checks WHERE report_hash = ?",
                (report["report_hash"],),
            ).fetchone()
            if row:
                return self._check(row)
            cursor = conn.execute(
                "INSERT INTO checks(declaration_path, declaration_hash,"
                " report_hash, report_json, operator, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (declaration_path, declaration_hash, report["report_hash"],
                 canonical_json(report), operator, created_at),
            )
            check_id = cursor.lastrowid
        return self.get_check(check_id)  # type: ignore[arg-type,return-value]

    def _check(self, row: sqlite3.Row) -> dict[str, Any]:
        record = self._decode(row, "report_json")
        payload = record.pop("report")
        payload.update(record)
        payload["kind"] = "check"
        return payload

    def get_check(self, check_id: int) -> dict[str, Any] | None:
        return self._fetch_one("checks", check_id, self._check)

    def list_checks(self) -> list[dict[str, Any]]:
        return self._fetch_all("checks", self._check)

    # ---- impacts --------------------------------------------------------
    def save_impact(self, left_refs: list[int], right_refs: list[int],
                    report: dict[str, Any], operator: str,
                    created_at: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM impacts WHERE report_hash = ?",
                (report["report_hash"],),
            ).fetchone()
            if row:
                return self._impact(row)
            cursor = conn.execute(
                "INSERT INTO impacts(left_refs_json, right_refs_json, report_hash,"
                " report_json, operator, created_at) VALUES(?,?,?,?,?,?)",
                (canonical_json(left_refs), canonical_json(right_refs),
                 report["report_hash"], canonical_json(report), operator,
                 created_at),
            )
            impact_id = cursor.lastrowid
        return self.get_impact(impact_id)  # type: ignore[arg-type,return-value]

    def _impact(self, row: sqlite3.Row) -> dict[str, Any]:
        record = self._decode(row, "report_json", "left_refs_json",
                              "right_refs_json")
        payload = record.pop("report")
        payload.update(record)
        payload["kind"] = "impact"
        return payload

    def get_impact(self, impact_id: int) -> dict[str, Any] | None:
        return self._fetch_one("impacts", impact_id, self._impact)

    def list_impacts(self) -> list[dict[str, Any]]:
        return self._fetch_all("impacts", self._impact)
