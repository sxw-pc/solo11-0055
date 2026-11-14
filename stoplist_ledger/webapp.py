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
        tables, compilations = store.list_tables(), store.list_compilations()
        snapshots, checks, impacts = (store.list_snapshots(),
                                      store.list_checks(), store.list_impacts())
        facts = {
            "table versions": store.summary()["table_versions"],
            "compilations": len(compilations),
            "snapshots": len(snapshots),
            "check runs": len(checks),
            "impact reports": len(impacts),
        }
        links = [
            {"href": url_for(endpoint), "label": label}
            for endpoint, label in (
                ("tables", "Browse table versions"),
                ("compilations", "Browse compilations"),
                ("snapshots", "Browse snapshots"),
                ("checks", "Browse check reports"),
                ("impacts", "Browse impact reports"),
            )
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

    def _list_route(path: str, title: str, endpoint: str,
                    list_call, link_call):
        @app.get(path, endpoint=endpoint)
        def route() -> str:
            records = list_call()
            return page(title, {"count": len(records)}, link_call(records),
                        records)

    def _detail_route(path: str, title: str, endpoint: str, get_call,
                      facts_call):
        @app.get(path, endpoint=endpoint)
        def route(record_id: int) -> str:
            record = get_call(record_id)
            if record is None:
                abort(404)
            return page(f"{title} {record_id}", facts_call(record),
                        payload=record)

    _list_route(
        "/tables", "Table versions", "tables", store.list_versions,
        lambda records: [
            {"href": url_for("table_detail", table_id=table["table_id"]),
             "label": table["table_id"]}
            for table in store.list_tables()
        ],
    )

    @app.get("/tables/<table_id>")
    def table_detail(table_id: str) -> str:
        records = store.list_versions(table_id)
        if not records:
            abort(404)
        facts = {"table": table_id, "versions": len(records),
                 "latest": records[-1]["version"]}
        return page(f"Table {table_id}", facts, payload=records)

    _list_route(
        "/compilations", "Compilations", "compilations",
        store.list_compilations,
        lambda records: [
            {"href": url_for("compilation_detail", record_id=item["id"]),
             "label": f"{item['name']} #{item['id']}"} for item in records
        ],
    )
    _detail_route(
        "/compilations/<int:record_id>", "Compilation", "compilation_detail",
        store.get_compilation,
        lambda record: {
            "name": record["name"], "scope": record["scope"],
            "rules": record["rule_count"], "rules hash": record["rules_hash"],
        },
    )
    _list_route(
        "/snapshots", "Snapshots", "snapshots", store.list_snapshots,
        lambda records: [
            {"href": url_for("snapshot_detail", record_id=item["id"]),
             "label": f"{item['text_id']} / {item['compilation']['name']} "
                      f"#{item['id']}"}
            for item in records
        ],
    )
    _detail_route(
        "/snapshots/<int:record_id>", "Snapshot", "snapshot_detail",
        store.get_snapshot,
        lambda record: {
            "text": record["text_id"],
            "compilation": record["compilation"]["name"],
            "tokens": record["summary"]["token_count"],
            "affected": record["summary"]["affected"],
            "result hash": record["result_hash"],
        },
    )
    _list_route(
        "/checks", "Consistency checks", "checks", store.list_checks,
        lambda records: [
            {"href": url_for("check_detail", record_id=item["id"]),
             "label": f"{item['declaration_path']} #{item['id']}"}
            for item in records
        ],
    )
    _detail_route(
        "/checks/<int:record_id>", "Check report", "check_detail",
        store.get_check,
        lambda record: {
            "status": record["status"],
            "issues": record["summary"]["total_issues"],
            "by code": record["summary"]["by_code"],
            "report hash": record["report_hash"],
        },
    )
    _list_route(
        "/impacts", "Impact reports", "impacts", store.list_impacts,
        lambda records: [
            {"href": url_for("impact_detail", record_id=item["id"]),
             "label": f"impact #{item['id']}"} for item in records
        ],
    )
    _detail_route(
        "/impacts/<int:record_id>", "Impact report", "impact_detail",
        store.get_impact,
        lambda record: {
            "left": record["left"]["compilation"]["name"],
            "right": record["right"]["compilation"]["name"],
            "affected texts": record["summary"]["affected_texts"],
            "affected tokens": record["summary"]["affected_tokens"],
            "report hash": record["report_hash"],
        },
    )

    # ---- JSON API -------------------------------------------------------
    @app.get("/api/summary")
    def api_summary():
        return jsonify(store.summary())

    @app.get("/api/tables/<table_id>")
    def api_table(table_id: str):
        records = store.list_versions(table_id)
        if not records:
            abort(404)
        return jsonify(records)

    api_routes = (
        ("/api/tables", "api_tables", store.list_versions),
        ("/api/compilations", "api_compilations", store.list_compilations),
        ("/api/compilations/<int:record_id>", "api_compilation",
         store.get_compilation),
        ("/api/snapshots", "api_snapshots", store.list_snapshots),
        ("/api/snapshots/<int:record_id>", "api_snapshot",
         store.get_snapshot),
        ("/api/checks", "api_checks", store.list_checks),
        ("/api/checks/<int:record_id>", "api_check", store.get_check),
        ("/api/impacts", "api_impacts", store.list_impacts),
        ("/api/impacts/<int:record_id>", "api_impact", store.get_impact),
    )
    for path, endpoint, call in api_routes:
        def make_view(call):
            def view(record_id: int | None = None):
                if record_id is None:
                    return jsonify(call())
                record = call(record_id)
                if record is None:
                    abort(404)
                return jsonify(record)
            return view
        app.add_url_rule(path, endpoint, make_view(call))

    return app
