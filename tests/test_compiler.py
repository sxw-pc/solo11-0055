from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES, compile_fixture, register_table
from stoplist_ledger.compiler import (
    build_compilation,
    load_declaration,
    table_in_scope,
)
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.util import canonical_json


def test_pipeline_a_rules_and_sources(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    assert compilation["name"] == "pipeline-a"
    assert compilation["priority"] == ["whitelist", "synonym", "noise", "zh_stop"]
    assert rules["alpha"]["disposition"] == "drop"
    assert rules["alpha"]["source"]["table_id"] == "zh_stop"
    assert rules["alpha"]["source"]["version"] == 2
    assert rules["alpha"]["source"]["rule"] == "stoplist:drop"
    assert rules["colour"]["disposition"] == "merge"
    assert rules["colour"]["target"] == "color"
    assert rules["projectalpha"]["disposition"] == "whitelist_exempt"
    assert compilation["rule_count"] == len(rules)
    assert compilation["tables"][0]["kind"] == "whitelist"


def test_compilation_is_deterministic(populated, clock) -> None:
    first = build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml"
    )
    second = build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml"
    )
    assert first["rules_hash"] == second["rules_hash"]
    assert canonical_json(first) == canonical_json(second)
    saved_a = compile_fixture(populated, clock)
    saved_b = compile_fixture(populated, clock)
    assert saved_a["id"] == saved_b["id"]
    assert len(populated.list_compilations()) == 1


def test_declaration_hash_is_stable(populated) -> None:
    path = FIXTURES / "declarations" / "pipeline_a.yaml"
    assert load_declaration(path)["declaration_hash"] == load_declaration(
        path
    )["declaration_hash"]


def test_priority_override_chain(tmp_path: Path, populated, clock) -> None:
    table_path = tmp_path / "override_syn_v1.yaml"
    table_path.write_text("alpha: alphacore\n", encoding="utf-8")
    register_table(populated, clock, table_path)
    declaration = {
        "name": "override-check",
        "scope": "lang-mix",
        "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
        "tables": [
            {"id": "conflict_stop", "version": 1, "scopes": ["*"]},
            {"id": "override_syn", "version": 1, "scopes": ["*"]},
        ],
        "priority": ["conflict_stop", "override_syn"],
        "merge_targets": ["alphacore"],
    }
    path = tmp_path / "override.yaml"
    path.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    compilation = build_compilation(populated, path)
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    assert rules["alpha"]["disposition"] == "drop"
    assert rules["alpha"]["overridden_by"][0]["disposition"] == "merge"
    assert rules["alpha"]["overridden_by"][0]["source"]["table_id"] == "override_syn"


def test_missing_table_version(tmp_path: Path, populated) -> None:
    declaration = {
        "name": "missing",
        "scope": "*",
        "tables": [{"id": "zh_stop", "version": 99, "scopes": ["*"]}],
        "priority": ["zh_stop"],
    }
    path = tmp_path / "missing.yaml"
    path.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_REF_NOT_FOUND"


def test_duplicate_priority(tmp_path: Path, populated) -> None:
    declaration = {
        "name": "duplicate-priority",
        "scope": "*",
        "tables": [{"id": "zh_stop", "version": 2, "scopes": ["*"]}],
        "priority": ["zh_stop", "zh_stop"],
    }
    path = tmp_path / "duplicate.yaml"
    path.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_DUP_PRIORITY"


def test_merge_target_allowlist(tmp_path: Path, populated) -> None:
    raw = yaml.safe_load(
        (FIXTURES / "declarations" / "pipeline_a.yaml").read_text(encoding="utf-8")
    )
    raw["merge_targets"] = ["color"]
    path = tmp_path / "narrow.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_MERGE_TARGET_NOT_ALLOWED"


def test_scope_filtering() -> None:
    assert table_in_scope("lang-mix", ["lang-zh", "lang-mix"])
    assert table_in_scope("lang-en", ["*"])
    assert table_in_scope("*", ["lang-zh"])
    assert not table_in_scope("lang-en", ["lang-zh"])


def test_unused_scoped_table_is_excluded(tmp_path: Path, populated) -> None:
    declaration = {
        "name": "scoped",
        "scope": "lang-en",
        "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
        "tables": [
            {"id": "zh_stop", "version": 2, "scopes": ["lang-zh"]},
            {"id": "whitelist", "version": 1, "scopes": ["*"]},
        ],
        "priority": ["zh_stop", "whitelist"],
        "merge_targets": ["color", "grey"],
    }
    path = tmp_path / "scoped.yaml"
    path.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    compilation = build_compilation(populated, path)
    assert compilation["priority"] == ["whitelist"]
    assert all(rule["source"]["table_id"] == "whitelist"
               for rule in compilation["rules"])


def _write_decl(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "mapping"],
        {"name": "x", "tables": []},
        {"name": "x", "tables": [{"id": "zh_stop", "version": 0}]},
        {"name": "x", "tables": [{"id": "zh_stop", "version": 2, "scopes": "all"}]},
        {"name": "x", "tables": [{"id": "zh_stop", "version": 2},
                                  {"id": "zh_stop", "version": 1}]},
        {"name": "x", "tables": [{"id": "zh_stop", "version": 2}],
         "priority": ["not_declared"]},
        {"name": "x", "tables": [{"id": "whitelist", "version": 1}],
         "priority": []},
        {"name": "x", "tables": [{"id": "whitelist", "version": 1}],
         "priority": ["whitelist"], "merge_targets": "color"},
    ],
)
def test_invalid_declaration_shapes(tmp_path, populated, payload) -> None:
    path = _write_decl(tmp_path, "invalid.yaml", payload)
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_DECL_INVALID"


def test_invalid_yaml_declaration(tmp_path, populated) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("tables: [unclosed\n", encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        load_declaration(path)
    assert caught.value.code == "E_DECL_INVALID"


def test_declaration_defaults(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "defaults.yaml",
        {"tables": [{"id": "whitelist", "version": 1}]},
    )
    declaration = load_declaration(path)
    assert declaration["name"] == "defaults"
    assert declaration["scope"] == "*"
    assert declaration["priority"] == ["whitelist"]
    assert declaration["normalization"] == {
        "case": "nfkc_lower", "whitespace": "collapse"
    }
    compilation = build_compilation(populated, path)
    assert compilation["priority"] == ["whitelist"]
    assert compilation["rule_count"] == 3


def test_unknown_priority_among_declared(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "unknown.yaml",
        {
            "tables": [{"id": "whitelist", "version": 1}],
            "priority": ["whitelist", "ghost"],
        },
    )
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_DECL_INVALID"


def test_compilation_table_trace_and_audit(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    trace = {table["id"]: table for table in compilation["tables"]}
    assert trace["zh_stop"]["content_hash"] == populated.get_version(
        "zh_stop", 2
    )["content_hash"]
    assert trace["noise"]["scopes"] == ["lang-mix"]
    events = [event["event"] for event in populated.list_audit()]
    assert "compilation.create" in events


def test_merge_target_normalization_in_rules(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "norm.yaml",
        {
            "name": "normalized-target",
            "scope": "*",
            "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
            "tables": [{"id": "synonym", "version": 1}],
            "priority": ["synonym"],
            "merge_targets": ["COLOR", "GREY"],
        },
    )
    compilation = build_compilation(populated, path)
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    assert rules["colour"]["target"] == "color"
    assert rules["gray"]["target"] == "grey"


def test_compilation_of_empty_table_has_no_rules(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "empty.yaml",
        {
            "name": "empty-compilation",
            "scope": "*",
            "tables": [{"id": "empty", "version": 1}],
            "priority": ["empty"],
        },
    )
    compilation = build_compilation(populated, path)
    assert compilation["rules"] == []
    assert compilation["disposition_counts"] == {
        "drop": 0, "merge": 0, "whitelist_exempt": 0
    }
