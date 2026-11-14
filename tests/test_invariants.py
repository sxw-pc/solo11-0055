from __future__ import annotations

import copy
import json
import random
import shutil
from pathlib import Path

from conftest import FIXTURES, compile_fixture, snapshot_set
from stoplist_ledger import demo as demo_module
from stoplist_ledger.clock import FixedClock
from stoplist_ledger.compiler import build_compilation, build_rules, load_declaration
from stoplist_ledger.demo import run_demo, safe_reset
from stoplist_ledger.impact import build_impact_payload
from stoplist_ledger.normalizer import NormalizationPolicy
from stoplist_ledger.registry import register_file
from stoplist_ledger.snapshot import build_snapshot, tokenize
from stoplist_ledger.util import canonical_json, stable_hash

INV = Path(__file__).resolve().parent / "invariant_fixtures"
RNG = random.Random(20240101)


def test_stable_hash_ignores_dict_key_order() -> None:
    ordered = {"b": 1, "a": [2, 3], "c": {"d": 4, "e": 5}}
    shuffled = {"c": {"e": 5, "d": 4}, "a": [2, 3], "b": 1}
    assert json.dumps(ordered) != json.dumps(shuffled)  # plain JSON keeps order
    assert stable_hash(ordered) == stable_hash(shuffled) == stable_hash(
        {"a": [2, 3], "b": 1, "c": {"d": 4, "e": 5}})


def test_rules_hash_survives_entry_order_shuffle(store, clock, monkeypatch) -> None:
    record = register_file(store, INV / "order_stop_v1.txt", clock,
                           table_id="order_stop")
    declaration = load_declaration(INV / "order_decl.yaml")
    terms = [r["term"] for r in build_compilation(
        store, INV / "order_decl.yaml")["rules"]]
    assert terms == sorted(terms) and len(terms) > 1
    shuffled = copy.deepcopy(record)
    RNG.shuffle(shuffled["entries"])
    assert [e["term"] for e in shuffled["entries"]] != terms
    versions = ({"order_stop": record}, {"order_stop": shuffled})
    rules_a, _, _ = build_rules(declaration, versions[0])
    rules_b, _, _ = build_rules(declaration, versions[1])
    assert canonical_json(rules_a) == canonical_json(rules_b)
    monkeypatch.setattr("stoplist_ledger.compiler.sorted",
                        lambda values, key=None, reverse=False: list(values),
                        raising=False)  # counter-example: drop pre-hash sort
    mutated, _, _ = build_rules(declaration, versions[1])
    assert canonical_json(mutated) != canonical_json(rules_a)

def test_snapshot_result_hash_embeds_compilation_normalization(
        store, clock, monkeypatch) -> None:
    register_file(store, INV / "norm_stop_v1.txt", clock, table_id="norm_stop")
    compilation = build_compilation(store, INV / "norm_decl.yaml")
    text = INV / "norm_sensitive.txt"
    first = build_snapshot(compilation, text)
    assert first["result_hash"] == build_snapshot(compilation, text)["result_hash"]
    policy = NormalizationPolicy.from_mapping(compilation["normalization"])
    raws = [t["raw"] for t in tokenize(text.read_text(encoding="utf-8"))]
    assert raws and all(policy.normalize_validated(r) != r for r in raws)
    assert all(t["term"] == policy.normalize_validated(t["raw"])
               for t in first["tokens"])  # embedded policy matches external one
    assert {t["term"] for t in first["tokens"]} <= {
        r["term"] for r in compilation["rules"]}
    monkeypatch.setattr(NormalizationPolicy, "normalize_validated",
                        lambda self, term: term)  # counter-example: no normalize
    broken = build_snapshot(compilation, text)
    assert broken["result_hash"] != first["result_hash"]
    assert all(t["disposition"] == "keep" for t in broken["tokens"])


def test_register_diff_line_numbers_are_self_consistent(store, clock) -> None:
    register_file(store, INV / "diffwords_v1.txt", clock, table_id="diffwords")
    current = register_file(store, INV / "diffwords_v2.txt", clock,
                            table_id="diffwords")
    diff, lines = current["diff"], [e["line"] for e in current["entries"]]
    assert lines == sorted(set(lines)) and set(diff["added"]) <= set(lines)
    assert all(c["new"] in lines for c in diff["modified"])
    counts = (diff["added_count"], diff["removed_count"], diff["modified_count"])
    assert counts == (1, 1, 1) == (len(diff["added"]), len(diff["removed"]),
                                   len(diff["modified"]))
    assert lines != list(range(1, len(lines) + 1))  # comments shift line numbers
    assert sorted(diff["added"] + [c["new"] for c in diff["modified"]]) == [3, 8]


def test_impact_symmetric_summary_fields_under_side_swap(populated, clock) -> None:
    comp_a = compile_fixture(populated, clock, "pipeline_a.yaml")
    ids_a = [s["id"] for s in snapshot_set(populated, clock, comp_a)]
    comp_b = compile_fixture(populated, clock, "pipeline_b.yaml")
    ids_b = [s["id"] for s in snapshot_set(populated, clock, comp_b)]
    forward = build_impact_payload(populated, ids_a, ids_b, clock.timestamp())
    swapped = build_impact_payload(populated, ids_b, ids_a, clock.timestamp())
    s1, s2 = forward["summary"], swapped["summary"]
    assert all(s1[k] == s2[k] for k in ("merge_target_changes",
               "exemption_changes", "affected_tokens", "affected_texts"))
    assert (s1["new_dropped_terms"], s1["gone_dropped_terms"]) == (
        s2["gone_dropped_terms"], s2["new_dropped_terms"])
    terms = lambda p, k: [c["term"] for c in p["rule_changes"][k]]
    assert terms(forward, "new_dropped") == terms(swapped, "gone_dropped")
    assert terms(forward, "gone_dropped") == terms(swapped, "new_dropped")
    assert terms(forward, "exemption_changes") == terms(swapped, "exemption_changes")
    assert min(s1["new_dropped_terms"], s1["gone_dropped_terms"],
               s1["merge_target_changes"], s1["exemption_changes"],
               s1["affected_texts"]) >= 1


def test_reset_and_rerun_demo_emits_identical_bytes_from_shuffled_inputs(
        tmp_path, monkeypatch) -> None:
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    table = root / "stoplists" / "zh_stop_v2.txt"
    body = table.read_text(encoding="utf-8").splitlines()
    header, terms = [body[0]], body[1:]
    shuffled = terms[:]
    RNG.shuffle(shuffled)
    assert shuffled != terms  # genuinely re-ordered input, not a trivial rerun
    table.write_text("\n".join(header + shuffled) + "\n", encoding="utf-8")
    monkeypatch.setattr(demo_module, "FIXTURES", root)
    var_dir, clock = tmp_path / "var", FixedClock("2024-01-01T00:00:00Z")
    run_demo(var_dir, clock, "op-invariant")
    snapshot = lambda: {p.name: p.read_bytes()
                        for p in (var_dir / "reports").iterdir()}
    first = snapshot()
    assert first  # demo emitted reports before the reset
    safe_reset(var_dir, var_dir, confirmed=True)
    assert not any(var_dir.iterdir())
    run_demo(var_dir, clock, "op-invariant")
    assert snapshot() == first  # byte-identical reports after a real reset
