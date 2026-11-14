"""Wildcard pattern support across registry, compiler and snapshot layers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stoplist_ledger.compiler import build_compilation
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.registry import register_file
from stoplist_ledger.snapshot import build_snapshot
from stoplist_ledger.store import LedgerStore
from stoplist_ledger.util import canonical_json


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _decl(tmp_path: Path, name: str, payload: dict) -> Path:
    return _write(tmp_path, name, yaml.safe_dump(payload, sort_keys=False))


def _wild_tables(store, clock, tmp_path: Path,
                 pat: str = "pre*\n*suf\n*mid*\n",
                 exact: str = "prefix\n") -> None:
    register_file(store, _write(tmp_path, "patstop_v1.txt", pat), clock,
                  table_id="patstop")
    register_file(store, _write(tmp_path, "exactstop_v1.txt", exact), clock,
                  table_id="exactstop")


def _wild_compilation(store, clock, tmp_path: Path,
                      priority=None) -> dict:
    _wild_tables(store, clock, tmp_path)
    path = _decl(tmp_path, "wild.yaml", {
        "name": "wild", "scope": "*",
        "tables": [{"id": "patstop", "version": 1},
                   {"id": "exactstop", "version": 1}],
        "priority": priority or ["patstop", "exactstop"],
    })
    return build_compilation(store, path)


# ---- registry layer --------------------------------------------------------

def test_pattern_entries_keep_shape_and_provenance(store, clock, tmp_path):
    record = register_file(
        store,         _write(tmp_path, "wild_v1.txt", "pre*\n*suf\n*mid*\nexact\n"), clock
    )
    shapes = {e["term"]: (e.get("pattern_kind"), e.get("needle"))
              for e in record["entries"]}
    assert shapes == {"pre*": ("prefix", "pre"), "*suf": ("suffix", "suf"),
                      "*mid*": ("substring", "mid"), "exact": (None, None)}
    first = record["entries"][0]
    assert first["line"] == 1 and first["raw"] == "pre*"
    assert record["term_count"] == 4


@pytest.mark.parametrize(
    "content", ["*\n", "a*b\n", "pre**\n", "**x\n", "x**\n", "**\n", "*a*b\n"]
)
def test_malformed_patterns_use_stable_code(store, clock, tmp_path, content):
    with pytest.raises(LedgerError) as caught:
        register_file(store, _write(tmp_path, "bad_v1.txt", content), clock)
    assert caught.value.code == "E_BAD_PATTERN"
    assert caught.value.details["raw"] == content.strip()
    assert caught.value.details["line"] == 1


def test_pattern_content_hash_is_line_order_independent(store, clock, tmp_path):
    first = register_file(
        store, _write(tmp_path, "a_v1.txt",
                      "alpha\npre*\n*suf\n*mid*\nbeta\n"),
        clock, table_id="mix")
    other = LedgerStore(tmp_path / "other.db").initialize()
    second = register_file(
        other, _write(tmp_path, "b_v1.txt",
                      "*mid*\nbeta\nalpha\n*suf\npre*\n"),
        clock, table_id="mix")
    assert first["content_hash"] == second["content_hash"]


def test_exact_table_content_hash_remains_legacy(store, clock, tmp_path):
    first = register_file(store, _write(tmp_path, "p_v1.txt", "alpha\nbeta\n"),
                          clock, table_id="pure")
    second = register_file(store, _write(tmp_path, "q_v1.txt", "beta\nalpha\n"),
                           clock, table_id="pure_alt")
    assert first["content_hash"] != second["content_hash"]


def test_star_in_yaml_synonym_is_literal_exact(store, clock, tmp_path):
    entry = register_file(
        store,         _write(tmp_path, "lit_v1.yaml", "pre*: target\n"), clock
    )["entries"][0]
    assert entry["term"] == "pre*" and "pattern_kind" not in entry


# ---- compiler layer --------------------------------------------------------

def test_compilation_builds_typed_pattern_index(store, clock, tmp_path):
    compilation = _wild_compilation(store, clock, tmp_path)
    rules = {r["term"]: r for r in compilation["rules"]}
    assert (rules["pre*"]["pattern_kind"], rules["pre*"]["needle"]) == (
        "prefix", "pre")
    assert rules["*suf"]["pattern_kind"] == "suffix"
    assert rules["*mid*"]["pattern_kind"] == "substring"
    assert "pattern_kind" not in rules["prefix"]
    index = compilation["pattern_index"]
    assert [r["term"] for r in index["prefix"]] == ["pre*"]
    assert [r["term"] for r in index["suffix"]] == ["*suf"]
    assert [r["term"] for r in index["substring"]] == ["*mid*"]


def test_pattern_compilation_is_deterministic(store, clock, tmp_path):
    compilation = _wild_compilation(store, clock, tmp_path)
    path = tmp_path / "wild.yaml"
    again = build_compilation(store, path)
    assert compilation["rules_hash"] == again["rules_hash"]
    assert canonical_json(compilation) == canonical_json(again)


def test_exact_only_compilation_has_no_pattern_index(populated):
    from conftest import FIXTURES

    compilation = build_compilation(
        populated, FIXTURES / "declarations" / "pipeline_a.yaml")
    assert "pattern_index" not in compilation


def test_pattern_compilation_records_priority_loser(store, clock, tmp_path):
    register_file(store, _write(tmp_path, "white_v1.txt", "pre*\n"), clock,
                  table_id="white", kind="whitelist")
    register_file(store, _write(tmp_path, "stop_v1.txt", "pre*\nprev*\n"), clock,
                  table_id="stop", kind="stoplist")
    path = _decl(tmp_path, "high.yaml", {
        "name": "high", "scope": "*",
        "tables": [{"id": "white", "version": 1},
                   {"id": "stop", "version": 1}],
        "priority": ["white", "stop"],
    })
    winner = next(r for r in build_compilation(store, path)["rules"]
                  if r["term"] == "pre*")
    assert winner["disposition"] == "whitelist_exempt"
    loser = winner["overridden_by"][0]
    assert (loser["disposition"], loser["match_type"], loser["term"]) == (
        "drop", "prefix", "pre*")


# ---- snapshot layer --------------------------------------------------------

def test_exact_rule_beats_higher_priority_pattern(store, clock, tmp_path):
    compilation = _wild_compilation(store, clock, tmp_path)
    text = _write(tmp_path, "tokens.txt",
                  "prefix preface xsuf midword keepme\n")
    by_term = {t["term"]: t for t in build_snapshot(compilation, text)["tokens"]}
    prefix = by_term["prefix"]
    assert prefix["match_type"] == "exact"
    assert prefix["source"]["table_id"] == "exactstop"
    loser = prefix["overridden_by"][0]
    assert (loser["match_type"], loser["term"]) == ("prefix", "pre*")
    assert loser["reason"] == "lower specificity than the winning exact rule"
    assert by_term["preface"]["match_type"] == "prefix"
    assert by_term["xsuf"]["match_type"] == "suffix"
    assert by_term["midword"]["match_type"] == "substring"
    assert by_term["keepme"]["disposition"] == "keep"
    assert by_term["keepme"]["match_type"] == "exact"


def test_specificity_sequence_prefix_suffix_substring(store, clock, tmp_path):
    register_file(store, _write(tmp_path, "spec_v1.txt", "pre*\n*suf\n*xs*\n"),
                  clock, table_id="spec")
    decl_path = _decl(tmp_path, "spec.yaml", {
        "name": "spec", "scope": "*",
        "tables": [{"id": "spec", "version": 1}], "priority": ["spec"]})
    text = _write(tmp_path, "spec.txt", "prexsuf\n")
    token = build_snapshot(build_compilation(store, decl_path), text)[
        "tokens"][0]
    assert token["match_type"] == "prefix"
    assert [e["match_type"] for e in token["overridden_by"]] == [
        "suffix", "substring"]
    assert all(e["reason"] == "lower pattern specificity than the winning rule"
               for e in token["overridden_by"])


def test_arbitration_uses_priority_then_term_order(store, clock, tmp_path):
    register_file(store, _write(tmp_path, "high_v1.txt", "ab*\n"), clock,
                  table_id="high_tbl")
    register_file(store, _write(tmp_path, "low_v1.txt", "ab*\nabd*\n"), clock,
                  table_id="low_tbl")

    def compile_with(priority):
        return build_compilation(store, _decl(
            tmp_path, "tie_" + "_".join(priority) + ".yaml",
            {"name": "tie", "scope": "*",
             "tables": [{"id": "high_tbl", "version": 1},
                        {"id": "low_tbl", "version": 1}],
             "priority": priority}))

    text = _write(tmp_path, "tie.txt", "abduct\n")
    priority_token = build_snapshot(
        compile_with(["high_tbl", "low_tbl"]), text)["tokens"][0]
    assert priority_token["source"]["table_id"] == "high_tbl"
    assert priority_token["overridden_by"][0]["reason"] == (
        "lower declared priority than the winning rule")
    order_token = build_snapshot(
        compile_with(["low_tbl", "high_tbl"]), text)["tokens"][0]
    assert (order_token["source"]["table_id"],
            order_token["source"]["raw"]) == ("low_tbl", "ab*")
    runtime = [e for e in order_token["overridden_by"]
               if e["reason"].startswith("later pattern")]
    assert runtime and runtime[0]["term"] == "abd*"


def test_wildcard_snapshot_is_deterministic(store, clock, tmp_path):
    compilation = _wild_compilation(store, clock, tmp_path)
    text = _write(tmp_path, "tokens.txt", "prefix preface\n")
    first, second = (build_snapshot(compilation, text),
                     build_snapshot(compilation, text))
    assert first == second and first["result_hash"] == second["result_hash"]


def test_exact_only_snapshot_has_no_match_type(populated, clock):
    from conftest import TEXTS, compile_fixture

    record = build_snapshot(compile_fixture(populated, clock), TEXTS[0])
    assert all("match_type" not in token for token in record["tokens"])
