from __future__ import annotations

import pytest

from conftest import FIXTURES
from stoplist_ledger.checker import check_declaration


def test_pipeline_a_is_clean(populated, clock) -> None:
    report = check_declaration(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml", clock
    )
    assert report["status"] == "pass"
    assert report["summary"]["total_issues"] == 0
    assert report["findings"] == []


def test_broken_declaration_triggers_all_eight_codes(populated, clock) -> None:
    report = check_declaration(
        populated, FIXTURES / "declarations" / "broken_conflict.yaml", clock
    )
    codes = {finding["code"] for finding in report["findings"]}
    assert codes == {f"C0{index}" for index in range(1, 9)}
    by_code = report["summary"]["by_code"]
    assert by_code["C01"] == 1
    assert by_code["C02"] == 1
    assert by_code["C03"] == 2
    assert report["status"] == "issues"
    subjects = {(finding["code"], finding["subject"])
                for finding in report["findings"]}
    assert ("C01", "alpha") in subjects
    assert ("C02", "xcycle -> ycycle -> xcycle") in subjects
    assert ("C04", "token") in subjects
    assert ("C05", "zh_stop@v99") in subjects
    assert ("C06", "conflict_stop") in subjects
    assert ("C07", "empty@v1") in subjects
    assert ("C08", "alpha") in subjects


def test_check_report_is_deterministic_and_saved(populated, clock) -> None:
    path = FIXTURES / "declarations" / "broken_conflict.yaml"
    first = check_declaration(populated, path, clock)
    second = check_declaration(populated, path, clock)
    assert first["id"] == second["id"]
    assert first["report_hash"] == second["report_hash"]
    assert len(populated.list_checks()) == 1
    assert first["generated_at"] == "2024-01-01T00:00:00Z"
    codes = [finding["code"] for finding in first["findings"]]
    assert codes == sorted(codes)


def test_check_report_markdown(populated, clock, tmp_path) -> None:
    from stoplist_ledger.report import write_report

    report = check_declaration(
        populated, FIXTURES / "declarations" / "broken_conflict.yaml", clock
    )
    path = write_report(report, "md", tmp_path / "check.md")
    text = path.read_text(encoding="utf-8")
    assert "C01" in text
    assert "C08" in text
    assert "No consistency issues detected." not in text


import yaml

from conftest import FIXTURES


def _declaration(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_broken_details_are_actionable(populated, clock) -> None:
    report = check_declaration(
        populated, FIXTURES / "declarations" / "broken_conflict.yaml", clock
    )
    findings = {(item["code"], item["subject"]): item for item in report["findings"]}
    c01 = findings[("C01", "alpha")]
    assert {proposal["table_id"] for proposal in c01["details"]["proposals"]} == {
        "conflict_stop", "broken_synonym"
    }
    c02 = findings[("C02", "xcycle -> ycycle -> xcycle")]
    assert c02["details"]["cycle"] == ["xcycle", "ycycle", "xcycle"]
    c08 = findings[("C08", "alpha")]
    assert c08["details"]["whitelist"][0]["table_id"] == "whitelist"
    assert c08["severity"] == "warning"


def test_case_variants_depend_on_policy(tmp_path, populated, clock) -> None:
    path = _declaration(
        tmp_path,
        "lower.yaml",
        {
            "name": "lower-check",
            "scope": "*",
            "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
            "tables": [
                {"id": "conflict_stop", "version": 1},
                {"id": "broken_synonym", "version": 1},
            ],
            "priority": ["conflict_stop", "broken_synonym"],
        },
    )
    report = check_declaration(populated, path, clock)
    assert "C04" not in {finding["code"] for finding in report["findings"]}


def test_missing_targets_can_be_allowed(tmp_path, populated, clock) -> None:
    path = _declaration(
        tmp_path,
        "allowed.yaml",
        {
            "name": "allowed-targets",
            "scope": "*",
            "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
            "tables": [{"id": "broken_synonym", "version": 1}],
            "priority": ["broken_synonym"],
            "merge_targets": [
                "alphacore", "ycycle", "xcycle", "missingtarget"
            ],
        },
    )
    report = check_declaration(populated, path, clock)
    codes = {finding["code"] for finding in report["findings"]}
    assert "C03" not in codes
    assert "C02" in codes


def test_scoped_out_missing_version_is_ignored(tmp_path, populated, clock) -> None:
    path = _declaration(
        tmp_path,
        "scoped-missing.yaml",
        {
            "name": "scoped-missing",
            "scope": "lang-en",
            "tables": [
                {"id": "zh_stop", "version": 99, "scopes": ["lang-zh"]},
                {"id": "whitelist", "version": 1, "scopes": ["*"]},
            ],
            "priority": ["zh_stop", "whitelist"],
        },
    )
    report = check_declaration(populated, path, clock)
    assert "C05" not in {finding["code"] for finding in report["findings"]}


def test_missing_version_is_error_severity(tmp_path, populated, clock) -> None:
    path = _declaration(
        tmp_path,
        "missing-only.yaml",
        {
            "name": "missing-only",
            "scope": "*",
            "tables": [{"id": "zh_stop", "version": 999}],
            "priority": ["zh_stop"],
        },
    )
    report = check_declaration(populated, path, clock)
    finding = next(item for item in report["findings"] if item["code"] == "C05")
    assert finding["severity"] == "error"
    assert finding["details"] == {"table_id": "zh_stop", "version": 999}


def test_pipeline_b_check_is_clean(populated, clock) -> None:
    report = check_declaration(
        populated, FIXTURES / "declarations" / "pipeline_b.yaml", clock
    )
    assert report["status"] == "pass"
    assert report["summary"]["total_issues"] == 0


def test_check_audit_event(populated, clock) -> None:
    check_declaration(populated, FIXTURES / "declarations" / "pipeline_a.yaml",
                      clock)
    assert "check.run" in [event["event"] for event in populated.list_audit()]


def test_check_unknown_declaration_raises(store, clock, tmp_path) -> None:
    from stoplist_ledger.errors import LedgerError

    with pytest.raises(LedgerError) as caught:
        check_declaration(store, tmp_path / "absent.yaml", clock)
    assert caught.value.code == "E_IO"
