from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES
from stoplist_ledger.compiler import build_compilation, load_declaration
from stoplist_ledger.errors import LedgerError

SCOPED = FIXTURES / "declarations" / "pipeline_scoped.yaml"
_POLICY = {"case": "nfkc_lower", "whitespace": "collapse"}
_TABLES = [
    {"id": "zh_stop", "version": 2, "scopes": ["lang-zh", "lang-mix"]},
    {"id": "synonym", "version": 1, "scopes": ["*"]},
    {"id": "noise", "version": 1, "scopes": ["lang-mix"]},
    {"id": "whitelist", "version": 1, "scopes": ["*"]},
]

def _write(tmp_path: Path, name: str, override: dict) -> Path:
    payload = {
        "name": "scoped", "scope": "lang-mix", "normalization": _POLICY,
        "tables": _TABLES,
        "priority": ["whitelist", "synonym", "noise", "zh_stop"],
        "merge_targets": ["color", "grey"],
        "scope_overrides": {"lang-zh": override},
    }
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path

def test_override_parsed_into_declaration() -> None:
    declaration = load_declaration(SCOPED)
    assert set(declaration["scope_overrides"]) == {
        "lang-zh", "lang-zh-redeclare", "lang-mix"
    }
    assert declaration["scope_overrides"]["lang-zh"]["priority"][0] == "zh_stop"

def test_restating_override_keeps_rules_hash(populated) -> None:
    baseline = build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml"
    )
    restated = build_compilation(populated, SCOPED, "lang-mix")
    assert restated["rules_hash"] == baseline["rules_hash"]
    assert restated["rules"] == baseline["rules"]
    assert set(restated["rule_origins"].values()) == {"override"}
    assert restated["scope_override"]["priority"] == {
        "semantics": "replace", "layer": "override"
    }

def test_no_override_payload_is_legacy_shape(populated) -> None:
    legacy = build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml"
    )
    assert "scope_override" not in legacy
    assert "rule_origins" not in legacy
    assert all("layer" not in table for table in legacy["tables"])

def test_priority_replace_and_merge_target_union(populated) -> None:
    compilation = build_compilation(populated, SCOPED, "lang-zh")
    assert compilation["priority"] == ["zh_stop", "whitelist", "synonym"]
    assert compilation["merge_targets"] == ["color", "grey", "silver"]
    assert {t["id"]: t["layer"] for t in compilation["tables"]} == {
        "zh_stop": "base", "whitelist": "base", "synonym": "base"
    }
    assert compilation["rule_origins"]["alpha"] == "base"
    contract = compilation["scope_override"]
    assert contract["merge_targets"] == {"semantics": "union"}
    assert contract["tables"]["noise"] == {"operation": "base", "layer": "base"}

def test_override_add_replace_remove(populated, tmp_path: Path) -> None:
    path = _write(tmp_path, "ops.yaml", {
        "tables": [
            {"id": "whitelist", "version": 2, "scopes": ["*"]},
            {"id": "noise", "remove": True},
        ],
        "priority": ["zh_stop", "whitelist", "synonym"],
    })
    compilation = build_compilation(populated, path, "lang-zh")
    tables = compilation["scope_override"]["tables"]
    assert tables["whitelist"] == {"operation": "replace", "layer": "override"}
    assert tables["noise"] == {"operation": "remove", "layer": "base"}
    assert "noise" not in {table["id"] for table in compilation["tables"]}
    trace = {table["id"]: table for table in compilation["tables"]}
    assert trace["whitelist"]["version"] == 2
    assert trace["whitelist"]["layer"] == "override"

def test_override_priority_unknown_table_is_decl_invalid(
    populated, tmp_path: Path
) -> None:
    path = _write(tmp_path, "ghost.yaml",
                  {"priority": ["zh_stop", "ghost_table"]})
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path, "lang-zh")
    assert caught.value.code == "E_DECL_INVALID"
    assert caught.value.details["layer"] == "override"
    assert caught.value.details["tables"] == ["ghost_table"]
    assert caught.value.details["scope"] == "lang-zh"

def test_override_remove_unknown_table_is_decl_invalid(
    populated, tmp_path: Path
) -> None:
    path = _write(tmp_path, "remove_ghost.yaml", {
        "tables": [{"id": "ghost_table", "remove": True}],
        "priority": ["whitelist", "synonym", "noise", "zh_stop"],
    })
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path, "lang-zh")
    assert caught.value.code == "E_DECL_INVALID"
    assert caught.value.details["layer"] == "override"
    assert caught.value.details["table"] == "ghost_table"

def test_override_added_table_missing_registration(
    populated, tmp_path: Path
) -> None:
    path = _write(tmp_path, "unreg.yaml", {
        "tables": [{"id": "never_registered", "version": 1, "scopes": ["*"]}],
        "priority": ["zh_stop", "never_registered", "whitelist",
                     "synonym", "noise"],
    })
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path, "lang-zh")
    assert caught.value.code == "E_REF_NOT_FOUND"
    assert caught.value.details["table_id"] == "never_registered"

@pytest.mark.parametrize(
    "override",
    [
        {"priority": "not-a-list"},
        {"tables": []},
        {"unknown_field": 1},
        {"tables": [{"id": "zh_stop", "version": 0}]},
        {"merge_targets": "color"},
    ],
)
def test_malformed_override_shapes(populated, tmp_path: Path, override) -> None:
    path = _write(tmp_path, "shape.yaml", override)
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path, "lang-zh")
    assert caught.value.code in {"E_SCOPE_OVERRIDE_INVALID", "E_DECL_INVALID"}

def test_malformed_scope_overrides_root(populated, tmp_path: Path) -> None:
    path = tmp_path / "root.yaml"
    path.write_text(
        yaml.safe_dump({"name": "x", "scope": "*",
                        "tables": [{"id": "zh_stop", "version": 2}],
                        "priority": ["zh_stop"],
                        "scope_overrides": "not-a-mapping"}),
        encoding="utf-8",
    )
    with pytest.raises(LedgerError) as caught:
        load_declaration(path)
    assert caught.value.code == "E_SCOPE_OVERRIDE_INVALID"

def test_unknown_scope_falls_back_to_base(populated) -> None:
    base_scope = build_compilation(populated, SCOPED)
    scoped = build_compilation(populated, SCOPED, "lang-fr")
    assert scoped["scope"] == "lang-fr"
    assert "scope_override" not in scoped
    assert scoped["declaration_hash"] == base_scope["declaration_hash"]
    wildcard = {table["id"] for table in base_scope["tables"]
                if "*" in table["scopes"] or "all" in table["scopes"]}
    assert {table["id"] for table in scoped["tables"]} == wildcard

def test_override_merge_targets_reset(populated, tmp_path: Path) -> None:
    path = _write(tmp_path, "reset.yaml", {
        "merge_targets": None,
        "priority": ["zh_stop", "whitelist", "synonym", "noise"],
    })
    compilation = build_compilation(populated, path, "lang-zh")
    assert compilation["merge_targets"] is None
    assert compilation["scope_override"]["merge_targets"] == {
        "semantics": "reset"
    }
