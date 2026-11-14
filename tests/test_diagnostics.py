from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES, compile_fixture
from stoplist_ledger.compiler import (
    build_compilation, compile_declaration, load_declaration,
)
from stoplist_ledger.diagnostics import (
    SCOPE_WARNING_CODES, diagnose_declaration_scopes, diagnose_scopes,
)
from stoplist_ledger.errors import LedgerError


def _write_decl(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _ids(tables: list[dict]) -> list[str]:
    return [table["id"] for table in tables]


def _codes(diagnosis: dict) -> list[str]:
    return [warning["code"] for warning in diagnosis["warnings"]]


def test_warning_codes_are_stable_and_complete() -> None:
    assert set(SCOPE_WARNING_CODES) == {
        "W_SCOPE_EXCLUDED", "W_SCOPE_NO_MATCH",
        "W_SCOPE_EMPTY", "W_SCOPE_UNKNOWN_LABEL"}


def test_partial_intersection_is_excluded_with_locator(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "partial.yaml", {
        "name": "partial", "scope": "lang-en",
        "tables": [
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]},
            {"id": "whitelist", "version": 1, "scopes": ["*"]}],
        "priority": ["zh_stop", "whitelist"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert diagnosis["scope"] == "lang-en"
    assert _ids(diagnosis["active_tables"]) == ["whitelist"]
    assert _ids(diagnosis["excluded_tables"]) == ["zh_stop"]
    assert diagnosis["empty_scope_tables"] == []
    warning = next(w for w in diagnosis["warnings"]
                   if w["code"] == "W_SCOPE_EXCLUDED")
    assert warning["table_id"] == "zh_stop"
    assert warning["declaration_scope"] == "lang-en"
    assert warning["ref_scopes"] == ["lang-zh"]
    assert "W_SCOPE_NO_MATCH" not in _codes(diagnosis)


def test_all_tables_excluded_raises_no_match(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "allout.yaml", {
        "name": "all-out", "scope": "lang-en",
        "tables": [
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]},
            {"id": "noise", "version": 1, "scopes": ["lang-mix"]}],
        "priority": ["zh_stop", "noise"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert diagnosis["active_tables"] == []
    assert _ids(diagnosis["excluded_tables"]) == ["zh_stop", "noise"]
    assert _codes(diagnosis).count("W_SCOPE_EXCLUDED") == 2
    no_match = next(w for w in diagnosis["warnings"]
                    if w["code"] == "W_SCOPE_NO_MATCH")
    assert no_match["table_id"] is None
    assert no_match["declaration_scope"] == "lang-en"
    assert _ids(no_match["tables"]) == ["zh_stop", "noise"]


def test_empty_scopes_fallback_is_named_not_reported_as_exclusion(
        tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "empty_scopes.yaml", {
        "name": "empty-scopes", "scope": "lang-en",
        "tables": [{"id": "whitelist", "version": 1, "scopes": []}],
        "priority": ["whitelist"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert _ids(diagnosis["empty_scope_tables"]) == ["whitelist"]
    assert _ids(diagnosis["active_tables"]) == ["whitelist"]
    assert diagnosis["excluded_tables"] == []
    warning = diagnosis["warnings"][0]
    assert warning["code"] == "W_SCOPE_EMPTY"
    assert warning["fallback"] == ["*"]
    assert _codes(diagnosis) == ["W_SCOPE_EMPTY"]
    # The fallback itself remains the historical ['*'] representation.
    assert load_declaration(path)["tables"][0]["scopes"] == ["*"]


def test_unknown_scope_label_is_flagged(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "unknown_label.yaml", {
        "name": "unknown-label", "scope": "lang-en",
        "tables": [{"id": "zh_stop", "version": 2,
                    "scopes": ["lang-zh", "lag-typo"]}],
        "priority": ["zh_stop"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert "W_SCOPE_EXCLUDED" in _codes(diagnosis)
    unknown = next(w for w in diagnosis["warnings"]
                   if w["code"] == "W_SCOPE_UNKNOWN_LABEL")
    assert unknown["table_id"] == "zh_stop"
    assert unknown["unknown_labels"] == ["lag-typo", "lang-zh"]
    assert unknown["declaration_scope"] == "lang-en"
    assert unknown["ref_scopes"] == ["lang-zh", "lag-typo"]


def test_label_shared_with_active_table_is_not_unknown(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "shared_label.yaml", {
        "name": "shared-label", "scope": "lang-en",
        "tables": [
            {"id": "whitelist", "version": 1,
             "scopes": ["lang-en", "lang-zh"]},
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]}],
        "priority": ["whitelist", "zh_stop"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert "W_SCOPE_UNKNOWN_LABEL" not in _codes(diagnosis)
    assert _ids(diagnosis["active_tables"]) == ["whitelist"]


def test_wildcard_declaration_scope_never_warns(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "wild.yaml", {
        "name": "wild", "scope": "all",
        "tables": [
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]},
            {"id": "whitelist", "version": 1, "scopes": ["*"]}],
        "priority": ["zh_stop", "whitelist"]})
    diagnosis = diagnose_declaration_scopes(path)
    assert diagnosis["warnings"] == []
    assert _ids(diagnosis["active_tables"]) == ["zh_stop", "whitelist"]
    assert diagnosis["excluded_tables"] == []


def test_pipeline_a_and_b_are_clean_in_declaration_order() -> None:
    for name in ("pipeline_a.yaml", "pipeline_b.yaml"):
        path = FIXTURES / "declarations" / name
        diagnosis = diagnose_declaration_scopes(path)
        assert diagnosis["warnings"] == [], name
        assert diagnosis["excluded_tables"] == [], name
        assert _ids(diagnosis["active_tables"]) == [
            ref["id"] for ref in load_declaration(path)["tables"]]


def test_diagnose_scopes_accepts_loaded_declaration(tmp_path, populated) -> None:
    path = _write_decl(tmp_path, "inline.yaml", {
        "name": "inline", "scope": "lang-en",
        "tables": [{"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]}],
        "priority": ["zh_stop"]})
    diagnosis = diagnose_scopes(load_declaration(path))
    assert set(_codes(diagnosis)) == {
        "W_SCOPE_EXCLUDED", "W_SCOPE_NO_MATCH", "W_SCOPE_UNKNOWN_LABEL"}


def test_strict_mode_rejects_and_persists_nothing(tmp_path, store, clock) -> None:
    path = _write_decl(tmp_path, "strict_bad.yaml", {
        "name": "strict-bad", "scope": "lang-en",
        "tables": [{"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]}],
        "priority": ["zh_stop"]})
    with pytest.raises(LedgerError) as caught:
        compile_declaration(store, path, clock, "op-test-01", strict=True)
    assert caught.value.code == "E_DECL_INVALID"
    assert caught.value.details["warnings"][0]["code"] == "W_SCOPE_EXCLUDED"
    assert store.list_compilations() == []
    assert store.list_audit() == []
    assert store.summary()["compilations"] == 0


def test_strict_mode_accepts_clean_pipeline_and_output_is_unchanged(
        populated, clock) -> None:
    strict_saved = compile_declaration(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml", clock,
        "op-test-01", strict=True)
    default_saved = compile_fixture(populated, clock)
    for field in ("rules", "tables", "priority", "rules_hash",
                  "disposition_counts", "rule_count", "scope",
                  "declaration_hash", "normalization"):
        assert strict_saved[field] == default_saved[field]
    # Deterministic id: identical payload collapses to one stored row.
    assert len(populated.list_compilations()) == 1


def test_default_mode_keeps_compiling_warned_declaration(
        tmp_path, populated, clock) -> None:
    path = _write_decl(tmp_path, "lenient.yaml", {
        "name": "lenient", "scope": "lang-en",
        "tables": [
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]},
            {"id": "whitelist", "version": 1, "scopes": ["*"]}],
        "priority": ["zh_stop", "whitelist"]})
    saved = compile_declaration(populated, path, clock, "op-test-01")
    assert saved["priority"] == ["whitelist"]
    assert len(populated.list_compilations()) == 1


def test_strict_does_not_swallow_missing_version(tmp_path, populated, clock) -> None:
    path = _write_decl(tmp_path, "missing.yaml", {
        "name": "missing", "scope": "*",
        "tables": [{"id": "zh_stop", "version": 99, "scopes": ["*"]}],
        "priority": ["zh_stop"]})
    with pytest.raises(LedgerError) as caught:
        compile_declaration(populated, path, clock, "op-test-01", strict=True)
    assert caught.value.code == "E_REF_NOT_FOUND"
    assert populated.list_compilations() == []


def test_strict_does_not_swallow_dup_priority(tmp_path, populated, clock) -> None:
    path = _write_decl(tmp_path, "dup.yaml", {
        "name": "dup", "scope": "*",
        "tables": [{"id": "zh_stop", "version": 2, "scopes": ["*"]}],
        "priority": ["zh_stop", "zh_stop"]})
    with pytest.raises(LedgerError) as caught:
        compile_declaration(populated, path, clock, "op-test-01", strict=True)
    assert caught.value.code == "E_DUP_PRIORITY"


def test_build_compilation_payload_is_unchanged_by_diagnostics(
        tmp_path, populated) -> None:
    path = FIXTURES / "declarations" / "pipeline_a.yaml"
    payload = build_compilation(populated, path)
    assert all("_scopes_was_empty" not in table for table in payload["tables"])
    empty_path = _write_decl(tmp_path, "empty_marker.yaml", {
        "name": "empty-marker", "scope": "lang-en",
        "tables": [{"id": "whitelist", "version": 1, "scopes": []}],
        "priority": ["whitelist"]})
    refs = load_declaration(empty_path)["tables"]
    assert refs[0]["scopes"] == ["*"]
    assert refs[0]["_scopes_was_empty"] is True
    # The marker stays out of every persisted compilation field.
    empty_payload = build_compilation(populated, empty_path)
    assert all("_scopes_was_empty" not in table
               for table in empty_payload["tables"])
