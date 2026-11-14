from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES
from stoplist_ledger.compiler import build_compilation, compile_declaration, load_declaration
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.util import file_sha256, stable_hash

DECL = FIXTURES / "declarations"
CHILD = DECL / "pipeline_child.yaml"
BASE = DECL / "pipeline_base.yaml"


def _copy_chain(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "pipeline_base.yaml"
    child = tmp_path / "pipeline_child.yaml"
    base.write_text(BASE.read_text(encoding="utf-8"), encoding="utf-8")
    child.write_text(CHILD.read_text(encoding="utf-8"), encoding="utf-8")
    return base, child


def test_child_merges_tables_priority_and_targets(populated) -> None:
    d = load_declaration(CHILD)
    assert d["chain_depth"] == 2
    assert [r["id"] for r in d["tables"]] == [
        "zh_stop", "synonym", "noise", "whitelist", "conflict_stop"]
    zh = next(r for r in d["tables"] if r["id"] == "zh_stop")
    assert (zh["version"], zh["scopes"]) == (3, ["lang-zh", "lang-mix"])
    assert d["normalization"] == {"case": "lower", "whitespace": "collapse"}
    assert d["priority"] == [
        "whitelist", "conflict_stop", "synonym", "noise", "zh_stop"]
    assert d["merge_targets"] == ["color", "grey", "silver"]


def test_normalization_is_replaced_not_key_merged(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(yaml.safe_dump({
        "name": "base", "scope": "*",
        "normalization": {"case": "case_sensitive", "whitespace": "trim"},
        "tables": [{"id": "whitelist", "version": 1}],
        "priority": ["whitelist"]}), encoding="utf-8")
    (tmp_path / "child.yaml").write_text(yaml.safe_dump({
        "name": "child", "extends": "base.yaml",
        "overrides": {"normalization": {"case": "lower"}}}), encoding="utf-8")
    assert load_declaration(tmp_path / "child.yaml")["normalization"] == {
        "case": "lower", "whitespace": "collapse"}


def test_merge_targets_append_is_deduplicated(populated) -> None:
    targets = load_declaration(CHILD)["merge_targets"]
    assert targets == ["color", "grey", "silver"]
    assert len(set(targets)) == len(targets)


def test_resolved_chain_layers_and_provenance(populated) -> None:
    d = load_declaration(CHILD)
    assert [i["role"] for i in d["resolved_chain"]] == ["root", "child"]
    assert [i["name"] for i in d["resolved_chain"]] == [
        "pipeline-base", "pipeline-child"]
    assert all(len(i["file_sha256"]) == 64 for i in d["resolved_chain"])
    origins = d["field_origins"]
    assert origins["normalization"]["layer"] == 1
    assert origins["normalization"]["overridden"][0]["layer"] == 0
    assert origins["tables"]["zh_stop"]["layer"] == 1
    assert origins["tables"]["zh_stop"]["overridden"][0]["old_value"] == {
        "id": "zh_stop", "version": 2, "scopes": ["lang-zh", "lang-mix"]}
    assert origins["tables"]["noise"] == {
        "layer": 0, "layer_name": "pipeline-base", "overridden": []}
    assert origins["merge_targets"]["overridden"][0]["old_value"] == [
        "color", "grey"]


def test_child_compiles_against_resolved_tree(populated) -> None:
    compilation = build_compilation(populated, CHILD)
    trace = {t["id"]: t for t in compilation["tables"]}
    assert trace["zh_stop"]["version"] == 3 and "conflict_stop" in trace
    assert compilation["resolved_chain"][0]["path"].endswith("pipeline_base.yaml")


def test_rules_hash_changes_when_any_layer_changes(populated, tmp_path: Path) -> None:
    _, child = _copy_chain(tmp_path)
    first = build_compilation(populated, child)
    assert build_compilation(populated, child)["rules_hash"] == first["rules_hash"]
    base = tmp_path / "pipeline_base.yaml"
    base.write_text("# governance touch\n" + base.read_text(encoding="utf-8"),
                    encoding="utf-8")
    changed = build_compilation(populated, child)
    assert changed["rules_hash"] != first["rules_hash"]
    assert changed["declaration_hash"] != first["declaration_hash"]


def test_chain_file_edit_invalidates_saved_compilation(
        populated, clock, tmp_path: Path) -> None:
    _, child = _copy_chain(tmp_path)
    first = compile_declaration(populated, child, clock, "t")
    child.write_text(child.read_text(encoding="utf-8") + "# touch\n",
                     encoding="utf-8")
    second = compile_declaration(populated, child, clock, "t")
    assert second["id"] != first["id"]
    assert len(populated.list_compilations()) == 2


def test_declaration_hash_fingerprints_every_layer(
        populated, tmp_path: Path) -> None:
    _, child = _copy_chain(tmp_path)
    d = load_declaration(child)
    fingerprint = [{"path": i["path"], "file_sha256": i["file_sha256"]}
                   for i in d["resolved_chain"]]
    assert d["declaration_hash"] == stable_hash(fingerprint)
    child.write_text(child.read_text(encoding="utf-8").replace(
        "scope: lang-mix", "scope: lang-mix "), encoding="utf-8")
    changed = load_declaration(child)
    assert changed["declaration_hash"] != d["declaration_hash"]
    assert changed["resolved_chain"][1]["file_sha256"] == file_sha256(child)


def test_inherited_scope_mismatch_points_at_parent_layer(populated) -> None:
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, DECL / "scoped_child_en.yaml")
    assert caught.value.code == "E_DECL_INVALID"
    assert "inherited" in caught.value.message
    details = caught.value.details
    assert details["inherited"] is True
    assert details["tables"][0]["origin_layer"] == 0
    assert details["tables"][0]["origin_layer_name"] == "scoped-zh-base"


def _cycle_decl(tmp_path: Path, name: str, target: str) -> Path:
    path = tmp_path / name
    path.write_text(
        f"name: {name[:-5]}\nextends: {target}\noverrides:\n  scope: '*'\n",
        encoding="utf-8")
    return path


def test_self_and_transitive_extends_cycles(tmp_path: Path, populated) -> None:
    self_path = _cycle_decl(tmp_path, "self.yaml", "self.yaml")
    with pytest.raises(LedgerError) as caught:
        load_declaration(self_path)
    assert caught.value.code == "E_DECL_CYCLE"
    cycle = caught.value.details["cycle"]
    assert cycle[0] == cycle[-1]
    _cycle_decl(tmp_path, "a.yaml", "b.yaml")
    _cycle_decl(tmp_path, "b.yaml", "a.yaml")
    with pytest.raises(LedgerError) as caught:
        load_declaration(tmp_path / "a.yaml")
    assert caught.value.code == "E_DECL_CYCLE"
    assert caught.value.details["cycle"] == ["a.yaml", "b.yaml", "a.yaml"]


def test_missing_parent_and_bad_override_use_stable_codes(
        tmp_path: Path, populated) -> None:
    (tmp_path / "orphan.yaml").write_text(
        "name: orphan\nextends: ghost.yaml\noverrides:\n  scope: '*'\n",
        encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        load_declaration(tmp_path / "orphan.yaml")
    assert caught.value.code == "E_IO"
    (tmp_path / "base.yaml").write_text(BASE.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    (tmp_path / "bad.yaml").write_text(
        "name: bad\nextends: base.yaml\noverrides:\n  bogus: 1\n",
        encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        load_declaration(tmp_path / "bad.yaml")
    assert caught.value.code == "E_DECL_INVALID"


def test_extension_keys_are_only_valid_in_their_layer(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(BASE.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    for name, text in (
        ("null_extends.yaml",
         "name: x\nextends:\noverrides:\n  scope: '*'\n"),
        ("root_overrides.yaml",
         BASE.read_text(encoding="utf-8") + "overrides: {scope: '*'}\n"),
        ("child_tables.yaml",
         "name: x\nextends: base.yaml\ntables: []\n"),
    ):
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(LedgerError) as caught:
            load_declaration(tmp_path / name)
        assert caught.value.code == "E_DECL_INVALID", name


def test_child_priority_must_cover_effective_tables(
        tmp_path: Path, populated) -> None:
    (tmp_path / "base.yaml").write_text(BASE.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    (tmp_path / "child.yaml").write_text(yaml.safe_dump({
        "name": "child", "extends": "base.yaml",
        "overrides": {"priority": ["whitelist"]}}), encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, tmp_path / "child.yaml")
    assert caught.value.code == "E_DECL_INVALID"
    assert set(caught.value.details["tables"]) == {
        "zh_stop", "synonym", "noise"}


def test_markdown_report_renders_chain_and_provenance(populated) -> None:
    from stoplist_ledger.report import render_markdown

    text = render_markdown(build_compilation(populated, CHILD))
    assert "## Inheritance chain" in text
    assert "pipeline-base" in text and "pipeline-child" in text
    assert "### Field provenance" in text and "### Table provenance" in text
    single = render_markdown(build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml"))
    assert "## Inheritance chain" not in single


def test_three_layer_chain_accumulates_history(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(yaml.safe_dump({
        "name": "base", "scope": "*",
        "tables": [{"id": "whitelist", "version": 1}],
        "priority": ["whitelist"]}), encoding="utf-8")
    (tmp_path / "mid.yaml").write_text(yaml.safe_dump({
        "name": "mid", "extends": "base.yaml",
        "overrides": {"tables": [{"id": "whitelist", "version": 2}]}}),
        encoding="utf-8")
    (tmp_path / "leaf.yaml").write_text(yaml.safe_dump({
        "name": "leaf", "extends": "mid.yaml",
        "overrides": {"tables": [{"id": "whitelist", "version": 1,
                                  "scopes": ["lang-en"]}]}}), encoding="utf-8")
    d = load_declaration(tmp_path / "leaf.yaml")
    assert d["chain_depth"] == 3
    provenance = d["field_origins"]["tables"]["whitelist"]
    assert provenance["layer"] == 2
    assert [i["layer"] for i in provenance["overridden"]] == [0, 1]
    assert d["tables"][0]["scopes"] == ["lang-en"]
    assert [i["role"] for i in d["resolved_chain"]] == ["root", "child", "child"]
