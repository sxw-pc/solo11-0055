"""Read-only local audit UI and JSON API."""

from __future__ import annotations

from typing import Any

from flask import Flask, abort, jsonify, render_template, url_for

from .store import LedgerStore
from .util import pretty_json

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_bind_host(host: str) -> str:
    from .errors import fail

    if host not in LOOPBACK_HOSTS:
        fail("E_NOT_LOCAL", "serve may bind only to a loopback host", host=host)
    return host


def create_app(store: LedgerStore) -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.json.sort_keys = True

    def page(title: str, facts: dict[str, Any] | None = None,
             links: list[dict[str, str]] | None = None,
             payload: Any = None) -> str:
        return render_template(
            "index.html",
            title=title,
            facts=facts or {},
            links=links or [],
            payload_json=pretty_json(payload) if payload is not None else None,
            summary=store.summary(),
        )

    @app.get("/")
    def index() -> str:
        tables = store.list_tables()
        compilations = store.list_compilations()
        snapshots = store.list_snapshots()
        checks = store.list_checks()
        impacts = store.list_impacts()
        facts = {
            "table versions": store.summary()["table_versions"],
            "compilations": len(compilations),
            "snapshots": len(snapshots),
            "check runs": len(checks),
            "impact reports": len(impacts),
        }
        links = [
            {"href": url_for("tables"), "label": "Browse table versions"},
            {"href": url_for("compilations"), "label": "Browse compilations"},
            {"href": url_for("snapshots"), "label": "Browse snapshots"},
            {"href": url_for("checks"), "label": "Browse check reports"},
            {"href": url_for("impacts"), "label": "Browse impact reports"},
        ]
        payload = {
            "tables": tables,
            "recent_compilations": [
                {"id": item["id"], "name": item["name"],
                 "rules_hash": item["rules_hash"]}
                for item in compilations[-5:]
            ],
            "recent_checks": [
                {"id": item["id"], "status": item["status"],
                 "total_issues": item["summary"]["total_issues"]}
                for item in checks[-5:]
            ],
            "recent_impacts": [
                {"id": item["id"], "summary": item["summary"]}
                for item in impacts[-5:]
            ],
        }
        return page("Local audit overview", facts, links, payload)

    @app.get("/tables")
    def tables() -> str:
        records = store.list_versions()
        links = [
            {"href": url_for("table_detail", table_id=table["table_id"]),
             "label": table["table_id"]}
            for table in store.list_tables()
        ]
        return page("Table versions", {"versions": len(records)}, links, records)

    @app.get("/tables/<table_id>")
    def table_detail(table_id: str) -> str:
        records = store.list_versions(table_id)
        if not records:
            abort(404)
        facts = {
            "table": table_id,
            "versions": len(records),
            "latest": records[-1]["version"],
        }
        return page(f"Table {table_id}", facts, payload=records)

    @app.get("/compilations")
    def compilations() -> str:
        records = store.list_compilations()
        links = [
            {"href": url_for("compilation_detail", compilation_id=item["id"]),
             "label": f"{item['name']} #{item['id']}"}
            for item in records
        ]
        return page("Compilations", {"count": len(records)}, links, records)

    @app.get("/compilations/<int:compilation_id>")
    def compilation_detail(compilation_id: int) -> str:
        record = store.get_compilation(compilation_id)
        if record is None:
            abort(404)
        facts = {
            "name": record["name"], "scope": record["scope"],
            "rules": record["rule_count"], "rules hash": record["rules_hash"],
        }
        return page(f"Compilation {record['name']}", facts, payload=record)

    @app.get("/snapshots")
    def snapshots() -> str:
        records = store.list_snapshots()
        links = [
            {"href": url_for("snapshot_detail", snapshot_id=item["id"]),
             "label": f"{item['text_id']} / {item['compilation']['name']} "
                      f"#{item['id']}"}
            for item in records
        ]
        return page("Snapshots", {"count": len(records)}, links, records)

    @app.get("/snapshots/<int:snapshot_id>")
    def snapshot_detail(snapshot_id: int) -> str:
        record = store.get_snapshot(snapshot_id)
        if record is None:
            abort(404)
        facts = {
            "text": record["text_id"],
            "compilation": record["compilation"]["name"],
            "tokens": record["summary"]["token_count"],
            "affected": record["summary"]["affected"],
            "result hash": record["result_hash"],
        }
        return page(f"Snapshot {record['text_id']}", facts, payload=record)

    @app.get("/checks")
    def checks() -> str:
        records = store.list_checks()
        links = [
            {"href": url_for("check_detail", check_id=item["id"]),
             "label": f"{item['declaration_path']} #{item['id']}"}
            for item in records
        ]
        return page("Consistency checks", {"count": len(records)}, links, records)

    @app.get("/checks/<int:check_id>")
    def check_detail(check_id: int) -> str:
        record = store.get_check(check_id)
        if record is None:
            abort(404)
        facts = {
            "status": record["status"],
            "issues": record["summary"]["total_issues"],
            "by code": record["summary"]["by_code"],
            "report hash": record["report_hash"],
        }
        return page("Check report", facts, payload=record)

    @app.get("/impacts")
    def impacts() -> str:
        records = store.list_impacts()
        links = [
            {"href": url_for("impact_detail", impact_id=item["id"]),
             "label": f"impact #{item['id']}"}
            for item in records
        ]
        return page("Impact reports", {"count": len(records)}, links, records)

    @app.get("/impacts/<int:impact_id>")
    def impact_detail(impact_id: int) -> str:
        record = store.get_impact(impact_id)
        if record is None:
            abort(404)
        facts = {
            "left": record["left"]["compilation"]["name"],
            "right": record["right"]["compilation"]["name"],
            "affected texts": record["summary"]["affected_texts"],
            "affected tokens": record["summary"]["affected_tokens"],
            "report hash": record["report_hash"],
        }
        return page("Impact report", facts, payload=record)

    # ---- JSON API -------------------------------------------------------
    @app.get("/api/summary")
    def api_summary():
        return jsonify(store.summary())

    @app.get("/api/tables")
    def api_tables():
        return jsonify(store.list_versions())

    @app.get("/api/tables/<table_id>")
    def api_table(table_id: str):
        records = store.list_versions(table_id)
        if not records:
            abort(404)
        return jsonify(records)

    @app.get("/api/compilations")
    def api_compilations():
        return jsonify(store.list_compilations())

    @app.get("/api/compilations/<int:compilation_id>")
    def api_compilation(compilation_id: int):
        record = store.get_compilation(compilation_id)
        if record is None:
            abort(404)
        return jsonify(record)

    @app.get("/api/snapshots")
    def api_snapshots():
        return jsonify(store.list_snapshots())

    @app.get("/api/snapshots/<int:snapshot_id>")
    def api_snapshot(snapshot_id: int):
        record = store.get_snapshot(snapshot_id)
        if record is None:
            abort(404)
        return jsonify(record)

    @app.get("/api/checks")
    def api_checks():
        return jsonify(store.list_checks())

    @app.get("/api/checks/<int:check_id>")
    def api_check(check_id: int):
        record = store.get_check(check_id)
        if record is None:
            abort(404)
        return jsonify(record)

    @app.get("/api/impacts")
    def api_impacts():
        return jsonify(store.list_impacts())

    @app.get("/api/impacts/<int:impact_id>")
    def api_impact(impact_id: int):
        record = store.get_impact(impact_id)
        if record is None:
            abort(404)
        return jsonify(record)

    return app
