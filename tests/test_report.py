from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FIXTURES, TEXTS, compile_fixture
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.report import (
    default_report_path,
    render_markdown,
    write_report,
)
from stoplist_ledger.snapshot import snapshot_text
from stoplist_ledger.webapp import create_app, validate_bind_host


def test_json_report_is_stable_and_machine_readable(tmp_path: Path) -> None:
    payload = {"kind": "check", "items": [{"b": 2, "a": 1}], "status": "pass"}
    first = write_report(payload, "json", tmp_path / "one.json")
    second = write_report(payload, "json", tmp_path / "two.json")
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8")) == payload


def test_default_report_path_sanitizes() -> None:
    path = default_report_path(Path("var/reports"), "snapshot",
                               "pipeline-a/sample 01", "md")
    assert path.name == "snapshot_pipeline-a_sample_01.md"


def test_report_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(LedgerError) as caught:
        write_report({"kind": "demo"}, "html", tmp_path / "x.html")
    assert caught.value.code == "E_BAD_FORMAT"


def test_compilation_and_snapshot_markdown(populated, clock, tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    snapshot = snapshot_text(
        populated, compilation["id"], TEXTS[0], clock
    )
    compilation_text = render_markdown(compilation)
    snapshot_text_md = render_markdown(snapshot)
    assert "Effective rules" in compilation_text
    assert "Decision source" in compilation_text
    assert "Token trace" in snapshot_text_md
    assert "default:keep" in snapshot_text_md
    assert "http://" not in compilation_text
    assert "https://" not in snapshot_text_md


def test_markdown_cells_escape_pipes() -> None:
    payload = {
        "kind": "check",
        "status": "issues",
        "findings": [{
            "code": "C01", "name": "test", "severity": "warning",
            "subject": "a|b", "message": "line\nbreak", "details": {},
        }],
        "summary": {"total_issues": 1, "by_code": {"C01": 1}},
    }
    text = render_markdown(payload)
    assert "a\\|b" in text
    assert "line break" in text


def test_webapp_is_read_only_and_local(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    snapshot = snapshot_text(populated, compilation["id"], TEXTS[0], clock)
    app = create_app(populated)
    client = app.test_client()
    for route in [
        "/", "/tables", "/tables/zh_stop",
        "/compilations", f"/compilations/{compilation['id']}",
        "/snapshots", f"/snapshots/{snapshot['id']}",
        "/checks", "/impacts", "/api/summary", "/api/tables",
        "/api/compilations", f"/api/compilations/{compilation['id']}",
        "/api/snapshots", f"/api/snapshots/{snapshot['id']}",
        "/api/checks", "/api/impacts",
    ]:
        response = client.get(route)
        assert response.status_code == 200, route
    html = client.get("/").get_data(as_text=True)
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()
    assert client.post("/api/tables").status_code == 405
    assert client.get("/api/snapshots/99999").status_code == 404


def test_bind_host_is_loopback_only() -> None:
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(LedgerError) as caught:
        validate_bind_host("0.0.0.0")
    assert caught.value.code == "E_NOT_LOCAL"


def test_webapp_detail_pages_after_check_and_impact(populated, clock) -> None:
    from conftest import compile_fixture, snapshot_set
    from stoplist_ledger.checker import check_declaration
    from stoplist_ledger.impact import create_impact

    a = compile_fixture(populated, clock, "pipeline_a.yaml")
    snaps_a = snapshot_set(populated, clock, a)
    b = compile_fixture(populated, clock, "pipeline_b.yaml")
    snaps_b = snapshot_set(populated, clock, b)
    impact = create_impact(
        populated, [item["id"] for item in snaps_a],
        [item["id"] for item in snaps_b], clock
    )
    check = check_declaration(
        populated, FIXTURES / "declarations" / "broken_conflict.yaml", clock
    )
    client = create_app(populated).test_client()
    for route in [
        f"/checks/{check['id']}", "/checks",
        f"/impacts/{impact['id']}", "/impacts",
        f"/api/checks/{check['id']}", f"/api/impacts/{impact['id']}",
        f"/api/tables/zh_stop", f"/api/tables/unknown",
    ]:
        expected = 200
        if route.endswith("/unknown"):
            expected = 404
        assert client.get(route).status_code == expected, route
    check_page = client.get(f"/checks/{check['id']}").get_data(as_text=True)
    assert "C08" in check_page
    assert "broken_conflict" in check_page


def test_api_json_is_machine_readable(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    client = create_app(populated).test_client()
    response = client.get(f"/api/compilations/{compilation['id']}")
    assert response.is_json
    payload = response.get_json()
    assert payload["name"] == "pipeline-a"
    assert payload["rules_hash"] == compilation["rules_hash"]
    summary = client.get("/api/summary").get_json()
    assert summary["compilations"] == 1


def test_markdown_empty_check_list() -> None:
    text = render_markdown({
        "kind": "check", "status": "pass", "findings": [],
        "summary": {"total_issues": 0, "by_code": {}},
    })
    assert "No consistency issues detected." in text


def test_json_report_ends_with_newline(tmp_path) -> None:
    path = write_report({"kind": "demo", "counts": {"a": 1}}, "json",
                        tmp_path / "nested" / "report.json")
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert path.parent.is_dir()


def test_default_report_path_cannot_escape_directory() -> None:
    path = default_report_path(Path("var/reports"), "check", "../../etc/passwd",
                               "json")
    assert path.parent == Path("var/reports")
    assert "/" not in path.name
    assert "\\" not in path.name


def test_demo_summary_markdown() -> None:
    text = render_markdown({
        "kind": "demo",
        "generated_at": "2024-01-01T00:00:00Z",
        "counts": {"snapshots": 6},
        "artifacts": ["var/reports/demo_summary.json"],
        "notes": ["synthetic"],
    })
    assert "## Counts" in text
    assert "## Artifacts" in text
    assert "demo_summary.json" in text
    assert "## Notes" in text


def test_webapp_index_has_no_write_form(populated) -> None:
    html = create_app(populated).test_client().get("/").get_data(as_text=True)
    assert "<form" not in html.lower()
    assert "method=\"post\"" not in html.lower()
    assert "127.0.0.1" not in html


def test_missing_web_records_return_404(store) -> None:
    client = create_app(store).test_client()
    for route in [
        "/tables/unknown", "/compilations/999", "/snapshots/999",
        "/checks/999", "/impacts/999", "/api/compilations/999",
        "/api/checks/999", "/api/impacts/999",
    ]:
        assert client.get(route).status_code == 404, route
