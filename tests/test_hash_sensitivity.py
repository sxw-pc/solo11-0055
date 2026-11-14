"""Hash-sensitivity regression matrix for the stoplist ledger.

Every test states its expected hash direction and feeds a concrete
counter-example. rules_hash is compared only on the pure build_compilation
result (never save_compilation rows), so UNIQUE(name, rules_hash) cannot mask
missing sensitivity. All ledgers are fresh under tmp_path; var/ledger.db is
never opened.
"""
from __future__ import annotations
from pathlib import Path
import pytest
import yaml
from conftest import FIXTURES
from stoplist_ledger.clock import FixedClock
from stoplist_ledger.compiler import build_compilation, compile_declaration
from stoplist_ledger.demo import register_fixtures
from stoplist_ledger.impact import build_impact_payload
from stoplist_ledger.registry import register_file
from stoplist_ledger.snapshot import build_snapshot, snapshot_text
from stoplist_ledger.store import LedgerStore
from stoplist_ledger.util import stable_hash

CLOCK = FixedClock("2026-11-14T00:00:00Z")
OPERATOR = "op-sensitivity-01"
DECLARATIONS = FIXTURES / "declarations"
STOPLISTS = FIXTURES / "stoplists"
TEXTS = sorted((FIXTURES / "texts").glob("*.txt"))
# Pinned from the committed fixtures: a reproducible contract, not drift.
A_RULES_HASH = "93b72805fe4c5d9ff814a0a30e00da5f385a263f132559289a95f2c8eb655f7e"
B_RULES_HASH = "dfb56d09940fb6019e1293e5d2734eb57a9c06fece53284c10626b790fe563b9"
IMPACT_HASH = "d09a42d03a1682b7653c6c8d2570af7286c1919f430201982ff41c73ec6f895d"
NFKC = {"case": "nfkc_lower", "whitespace": "collapse"}
WHITELIST = [{"id": "whitelist", "version": 1, "scopes": ["*"]}]
BLOCK_YAML = ("name: n3\nscope: '*'\nnormalization:\n  case: nfkc_lower\n"
              "  whitespace: collapse\ntables:\n  - id: whitelist\n    version: 1\n"
              "    scopes: ['*']\npriority: [whitelist]\n")
FLOW_YAML = ("name: n3\nscope: '*'\n"
             "normalization: {case: \"nfkc_lower\", whitespace: 'collapse'}\n"
             "tables:\n  - {id: whitelist, version: 1, scopes: [\"*\"]}\n"
             "priority: [whitelist]\n")


@pytest.fixture
def ledger(tmp_path: Path) -> LedgerStore:
    store = LedgerStore(tmp_path / "sensitivity-ledger.db").initialize()
    register_fixtures(store, CLOCK, OPERATOR)
    return store


@pytest.fixture
def pipeline_a() -> dict:
    return yaml.safe_load((DECLARATIONS / "pipeline_a.yaml").read_text("utf-8"))


def _decl(tmp_path: Path, name: str, payload=None, raw=None) -> Path:
    path = tmp_path / name
    path.write_text(raw if raw is not None else yaml.safe_dump(payload, sort_keys=False),
                    encoding="utf-8")
    return path


def _compile(store, tmp_path, name, payload):
    return build_compilation(store, _decl(tmp_path, name, payload))


def _outcomes(c):
    return {r["term"]: (r["disposition"], r["target"]) for r in c["rules"]}


def _audit_order(s):
    return [e["subject"] for e in s.list_audit() if e["event"] == "table.register"]


def _content_hashes(c):
    return {t["id"]: t["content_hash"] for t in c["tables"]}


def _snapshot_all(store, c):
    return [snapshot_text(store, c["id"], t, CLOCK, OPERATOR) for t in TEXTS]


class TestRulesHashSensitivity:
    def test_tables_declaration_order_insensitive(self, tmp_path, ledger, pipeline_a):
        # Expect EQUAL. Counter-example: same four table refs reversed, priority
        # fixed; declaration_hash must move but rules_hash must not.
        base = _compile(ledger, tmp_path, "base.yaml", pipeline_a)
        variant = _compile(ledger, tmp_path, "reversed.yaml",
                           dict(pipeline_a, tables=list(reversed(pipeline_a["tables"]))))
        assert variant["declaration_hash"] != base["declaration_hash"]
        assert variant["rules_hash"] == base["rules_hash"] == A_RULES_HASH
        assert (variant["rules"], variant["priority"]) == (base["rules"], base["priority"])

    def test_file_registration_order_insensitive(self, tmp_path, ledger, pipeline_a):
        # Expect EQUAL. Counter-example: a second fresh ledger with files
        # registered in reverse name order (audit order asserted different).
        declaration = _decl(tmp_path, "shared.yaml", pipeline_a)
        forward = build_compilation(ledger, declaration)
        reversed_ledger = LedgerStore(tmp_path / "reversed.db").initialize()
        for path in sorted(STOPLISTS.iterdir(), key=lambda p: p.name, reverse=True):
            register_file(reversed_ledger, path, CLOCK, operator=OPERATOR, allow_existing=True)
        assert _audit_order(reversed_ledger) == list(reversed(_audit_order(ledger)))
        backward = build_compilation(reversed_ledger, declaration)
        assert _content_hashes(backward) == _content_hashes(forward)
        assert backward["rules_hash"] == forward["rules_hash"] == A_RULES_HASH

    def test_priority_order_sensitive(self, tmp_path, ledger, pipeline_a):
        # Expect DIFFERENT. Counter-example A: reversed relative priority of two
        # overlapping tables -> real "alpha" decision flip (drop vs merge).
        t_refs = [{"id": "conflict_stop", "version": 1, "scopes": ["*"]},
                  {"id": "broken_synonym", "version": 1, "scopes": ["*"]}]
        drop_first = {"name": "p-drop", "scope": "*", "normalization": NFKC,
                      "tables": t_refs, "priority": ["conflict_stop", "broken_synonym"]}
        merge_first = dict(drop_first, name="p-merge",
                           priority=["broken_synonym", "conflict_stop"])
        drop = _compile(ledger, tmp_path, "drop.yaml", drop_first)
        merge = _compile(ledger, tmp_path, "merge.yaml", merge_first)
        drop_rules = {r["term"]: r for r in drop["rules"]}
        merge_rules = {r["term"]: r for r in merge["rules"]}
        assert drop_rules["alpha"]["disposition"] == "drop"
        assert (merge_rules["alpha"]["disposition"], merge_rules["alpha"]["target"]) == (
            "merge", "alphacore")
        assert drop["rules_hash"] != merge["rules_hash"]
        # Counter-example B (order-only): reversed pipeline-a priority; tables
        # and outcomes unchanged, yet the hash must move.
        base = _compile(ledger, tmp_path, "pa.yaml", pipeline_a)
        variant = _compile(ledger, tmp_path, "pa_perm.yaml",
                           dict(pipeline_a, priority=list(reversed(pipeline_a["priority"]))))
        assert _outcomes(variant) == _outcomes(base)
        assert variant["rule_count"] == base["rule_count"]
        assert variant["rules_hash"] != base["rules_hash"] == A_RULES_HASH

    def test_normalization_equivalent_spelling_insensitive(self, tmp_path, ledger):
        # Expect EQUAL for (1) swapped normalization map keys, same values, and
        # (2) block vs flow YAML, same parsed values; fixed key re-serialization
        # means surface spelling never reaches the hash.
        canonical = {"name": "n1", "scope": "*", "normalization": NFKC,
                     "tables": WHITELIST, "priority": ["whitelist"]}
        swapped = dict(canonical, name="n2",
                       normalization={"whitespace": "collapse", "case": "nfkc_lower"})
        base = _compile(ledger, tmp_path, "n1.yaml", canonical)
        key_variant = _compile(ledger, tmp_path, "n2.yaml", swapped)
        assert key_variant["normalization"] == base["normalization"]
        assert (key_variant["rules_hash"], key_variant["rules"]) == (
            base["rules_hash"], base["rules"])
        block_base = build_compilation(ledger, _decl(tmp_path, "block.yaml", raw=BLOCK_YAML))
        spell_variant = build_compilation(ledger, _decl(tmp_path, "flow.yaml", raw=FLOW_YAML))
        assert BLOCK_YAML != FLOW_YAML
        assert (spell_variant["rules_hash"], spell_variant["rules"]) == (
            block_base["rules_hash"], block_base["rules"])

    def test_normalization_value_change_sensitive(self, tmp_path, ledger):
        # Expect DIFFERENT. Counter-example: same table under nfkc_lower vs
        # lower, everything else fixed; the policy is part of the hash input.
        nfkc = {"name": "p1", "scope": "*", "normalization": NFKC,
                "tables": WHITELIST, "priority": ["whitelist"]}
        lower = dict(nfkc, name="p2",
                     normalization={"case": "lower", "whitespace": "collapse"})
        base = _compile(ledger, tmp_path, "nfkc.yaml", nfkc)
        variant = _compile(ledger, tmp_path, "lower.yaml", lower)
        assert variant["normalization"] != base["normalization"]
        assert variant["rules_hash"] != base["rules_hash"]


class TestResultHashSensitivity:
    @pytest.fixture
    def compilation(self, tmp_path, ledger, pipeline_a):
        return build_compilation(ledger, _decl(tmp_path, "snapshot-base.yaml", pipeline_a))

    def test_repeated_build_and_snapshot_text_stable(self, tmp_path, ledger, compilation):
        # Expect EQUAL across two pure builds and two snapshot_text calls; also
        # equal an independently re-derived hash and the persisted row (not a
        # bare first == second assertion).
        text = TEXTS[0]
        first, second = build_snapshot(compilation, text), build_snapshot(compilation, text)
        semantic = {k: v for k, v in first.items() if k != "result_hash"}
        assert stable_hash(semantic) == first["result_hash"]
        assert second == first
        saved = compile_declaration(ledger, tmp_path / "snapshot-base.yaml", CLOCK, OPERATOR)
        rec1 = snapshot_text(ledger, saved["id"], text, CLOCK, OPERATOR)
        rec2 = snapshot_text(ledger, saved["id"], text, CLOCK, OPERATOR)
        assert rec2["id"] == rec1["id"]  # unique constraint dedups to one row
        assert len(ledger.list_snapshots(saved["id"])) == 1
        assert (rec1["result_hash"], rec1["tokens"]) == (
            first["result_hash"], first["tokens"])

    def test_trailing_newline_sensitive(self, tmp_path, compilation):
        # Expect DIFFERENT result_hash (text_hash commits to raw bytes) while
        # token decisions stay identical. Counter-example: same text with vs
        # without a terminal newline.
        body = TEXTS[0].read_text("utf-8").rstrip("\n")
        no_nl, with_nl = tmp_path / "no_newline.txt", tmp_path / "with_newline.txt"
        no_nl.write_text(body, encoding="utf-8")
        with_nl.write_text(body + "\n", encoding="utf-8")
        stripped, terminated = build_snapshot(compilation, no_nl), build_snapshot(
            compilation, with_nl)
        assert no_nl.read_bytes() != with_nl.read_bytes()
        assert stripped["text_hash"] != terminated["text_hash"]
        assert stripped["result_hash"] != terminated["result_hash"]
        assert (stripped["tokens"], stripped["summary"]) == (
            terminated["tokens"], terminated["summary"])

    def test_equivalent_raw_tokens_converge_but_stay_distinct(self, tmp_path, compilation):
        # Expect DIFFERENT result_hash (different raw text) but EQUAL normalized
        # term and decision. Counter-example 1 (case): "Alpha" vs "alpha";
        # counter-example 2 (whitespace): padded vs unpadded raw.
        upper, lower = tmp_path / "upper.txt", tmp_path / "lower.txt"
        upper.write_text("Alpha\n", encoding="utf-8")
        lower.write_text("alpha\n", encoding="utf-8")
        snap_upper, snap_lower = build_snapshot(compilation, upper), build_snapshot(
            compilation, lower)
        tu, tl = snap_upper["tokens"][0], snap_lower["tokens"][0]
        assert (tu["raw"], tl["raw"]) == ("Alpha", "alpha")
        assert tu["term"] == tl["term"] == "alpha"
        assert tu["disposition"] == tl["disposition"] == "drop"
        assert tu["target"] == tl["target"]
        assert snap_upper["result_hash"] != snap_lower["result_hash"]
        padded, trimmed = tmp_path / "padded.txt", tmp_path / "trimmed.txt"
        padded.write_text("  noiseblock   \n", encoding="utf-8")
        trimmed.write_text("noiseblock\n", encoding="utf-8")
        sp, st = build_snapshot(compilation, padded), build_snapshot(compilation, trimmed)
        assert sp["tokens"][0]["term"] == st["tokens"][0]["term"] == "noiseblock"
        assert sp["tokens"][0]["disposition"] == st["tokens"][0]["disposition"]
        assert sp["result_hash"] != st["result_hash"]


class TestImpactReportHash:
    def test_report_hash_baseline_and_input_sensitivity(self, tmp_path, ledger, pipeline_a):
        # Expect the two-compilation report_hash to equal the pinned baseline,
        # reproduce on an independent fresh ledger, and CHANGE when the hashed
        # generated_at input moves.
        decl_a = _decl(tmp_path, "pipeline_a.yaml", pipeline_a)
        pure_a = build_compilation(ledger, decl_a)
        pure_b = build_compilation(ledger, DECLARATIONS / "pipeline_b.yaml")
        assert (pure_a["rules_hash"], pure_b["rules_hash"]) == (A_RULES_HASH, B_RULES_HASH)
        comp_a = compile_declaration(ledger, decl_a, CLOCK, OPERATOR)
        comp_b = compile_declaration(ledger, DECLARATIONS / "pipeline_b.yaml", CLOCK, OPERATOR)
        assert (comp_a["rules_hash"], comp_b["rules_hash"]) == (
            pure_a["rules_hash"], pure_b["rules_hash"])
        snaps_a, snaps_b = _snapshot_all(ledger, comp_a), _snapshot_all(ledger, comp_b)
        ids_a, ids_b = [s["id"] for s in snaps_a], [s["id"] for s in snaps_b]
        payload = build_impact_payload(ledger, ids_a, ids_b, CLOCK.timestamp())
        assert payload["report_hash"] == IMPACT_HASH
        # Independent ledger: same fixtures, fixed timestamp, same commitment.
        replica = LedgerStore(tmp_path / "replica.db").initialize()
        register_fixtures(replica, CLOCK, OPERATOR)
        ra = compile_declaration(replica, decl_a, CLOCK, OPERATOR)
        rb = compile_declaration(replica, DECLARATIONS / "pipeline_b.yaml", CLOCK, OPERATOR)
        rsa, rsb = _snapshot_all(replica, ra), _snapshot_all(replica, rb)
        replica_payload = build_impact_payload(
            replica, [s["id"] for s in rsa], [s["id"] for s in rsb], CLOCK.timestamp())
        assert [s["id"] for s in rsa] == ids_a  # snapshot ids deterministic
        assert (replica_payload["report_hash"], replica_payload["summary"]) == (
            payload["report_hash"], payload["summary"])
        later = build_impact_payload(ledger, ids_a, ids_b, "2026-11-15T00:00:00Z")
        assert later["report_hash"] != payload["report_hash"]
