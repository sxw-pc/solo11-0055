from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FIXTURES, TEXTS, compile_fixture
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.snapshot import (
    build_snapshot,
    snapshot_text,
    tokenize,
)

def test_tokenize_offsets_and_punctuation() -> None:
    tokens = tokenize(" Alpha,beta\n gamma!")
    assert tokens == [
        {"raw": "Alpha", "start": 1, "end": 6},
        {"raw": "beta", "start": 7, "end": 11},
        {"raw": "gamma", "start": 13, "end": 18},
    ]

def test_snapshot_trace_and_golden(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    record = snapshot_text(
        populated, compilation["id"], TEXTS[0], clock, "op-test-01"
    )
    expected = json.loads(
        (FIXTURES / "expected" / "snapshot_01.json").read_text(encoding="utf-8")
    )
    for key, value in expected.items():
        assert record[key] == value, key
    assert record["summary"] == {
        "token_count": 7,
        "keep": 1,
        "drop": 3,
        "merge": 2,
        "whitelist_exempt": 1,
        "affected": 6,
    }
    tokens = record["tokens"]
    assert tokens[0]["raw"] == "Alpha"
    assert (tokens[0]["start"], tokens[0]["end"]) == (0, 5)
    assert tokens[0]["term"] == "alpha"
    assert tokens[0]["disposition"] == "drop"
    assert tokens[0]["source"]["table_id"] == "zh_stop"
    assert tokens[2]["source"]["rule"] == "default:keep"
    assert tokens[3]["target"] == "color"
    assert tokens[6]["disposition"] == "whitelist_exempt"

def test_snapshot_hash_is_stable(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    first = build_snapshot(compilation, TEXTS[0])
    second = build_snapshot(compilation, TEXTS[0])
    assert first == second
    assert first["result_hash"] == second["result_hash"]
    saved_a = snapshot_text(populated, compilation["id"], TEXTS[0], clock)
    saved_b = snapshot_text(populated, compilation["id"], TEXTS[0], clock)
    assert saved_a["id"] == saved_b["id"]
    assert len(populated.list_snapshots(compilation["id"])) == 1

def test_snapshot_scope_must_match(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    with pytest.raises(LedgerError) as caught:
        build_snapshot(compilation, TEXTS[0], scope="lang-zh")
    assert caught.value.code == "E_BAD_FORMAT"

def test_snapshot_missing_compilation(store, clock) -> None:
    with pytest.raises(LedgerError) as caught:
        snapshot_text(store, 999, TEXTS[0], clock)
    assert caught.value.code == "E_REF_NOT_FOUND"

def test_empty_text_snapshot(populated, clock, tmp_path: Path) -> None:
    compilation = compile_fixture(populated, clock)
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    record = snapshot_text(populated, compilation["id"], empty, clock)
    assert record["tokens"] == []
    assert record["summary"]["token_count"] == 0
    assert record["summary"]["keep"] == 0

def test_all_fixture_texts_are_traceable(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    for text in TEXTS:
        record = snapshot_text(populated, compilation["id"], text, clock)
        assert record["summary"]["token_count"] > 0
        assert record["compilation"]["rules_hash"] == compilation["rules_hash"]
        assert all({"index", "start", "end", "disposition", "source"}
                   <= set(token) for token in record["tokens"])

def test_tokenize_unicode_and_symbols() -> None:
    tokens = tokenize("中文，符号！ abc_123 x+y")
    assert [token["raw"] for token in tokens] == ["中文", "符号", "abc_123", "x+y"]
    assert tokens[0]["start"] == 0
    assert tokens[-1]["end"] == len("中文，符号！ abc_123 x+y")

def test_keep_token_has_default_source(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    record = snapshot_text(populated, compilation["id"], TEXTS[0], clock)
    gamma = next(token for token in record["tokens"] if token["term"] == "gamma")
    assert gamma["disposition"] == "keep"
    assert gamma["source"]["kind"] == "default"
    assert gamma["source"]["table_id"] is None
    assert gamma["source"]["rule"] == "default:keep"
    assert gamma["overridden_by"] == []

def test_snapshot_includes_override_chain(tmp_path, populated) -> None:
    import yaml

    from conftest import FIXTURES
    from stoplist_ledger.compiler import build_compilation

    declaration = {
        "name": "override-snapshot",
        "scope": "*",
        "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
        "tables": [
            {"id": "conflict_stop", "version": 1},
            {"id": "broken_synonym", "version": 1},
        ],
        "priority": ["conflict_stop", "broken_synonym"],
    }
    decl_path = tmp_path / "override.yaml"
    decl_path.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    compilation = build_compilation(populated, decl_path)
    text = tmp_path / "override.txt"
    text.write_text("alpha\n", encoding="utf-8")
    record = build_snapshot(compilation, text)
    token = record["tokens"][0]
    assert token["disposition"] == "drop"
    assert len(token["overridden_by"]) == 1
    assert token["overridden_by"][0]["disposition"] == "merge"

def test_snapshot_stores_text_hash_and_source(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    record = snapshot_text(populated, compilation["id"], TEXTS[1], clock)
    assert len(record["text_hash"]) == 64
    assert record["source_path"].endswith("sample_02.txt")
    assert record["compilation"]["name"] == "pipeline-a"
    assert record["schema_version"] == 1

def test_snapshot_missing_text(populated, clock, tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    with pytest.raises(LedgerError) as caught:
        snapshot_text(populated, compilation["id"], tmp_path / "no.txt", clock)
    assert caught.value.code == "E_IO"

def test_snapshot_audit_event(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    snapshot_text(populated, compilation["id"], TEXTS[0], clock)
    events = [event["event"] for event in populated.list_audit()]
    assert "snapshot.create" in events

def test_snapshot_many_sorts_by_filename(populated, clock) -> None:
    from conftest import snapshot_set

    compilation = compile_fixture(populated, clock)
    records = snapshot_set(populated, clock, compilation)
    assert [record["text_id"] for record in records] == [
        "sample_01", "sample_02", "sample_03"
    ]
    assert [record["id"] for record in records] == sorted(
        record["id"] for record in records
    )

def test_each_disposition_appears_in_fixture_snapshots(populated, clock) -> None:
    from conftest import snapshot_set

    compilation = compile_fixture(populated, clock)
    dispositions = set()
    for record in snapshot_set(populated, clock, compilation):
        dispositions.update(token["disposition"] for token in record["tokens"])
    assert dispositions == {"keep", "drop", "merge", "whitelist_exempt"}

def test_token_offsets_cover_every_raw_token(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    text = TEXTS[2].read_text(encoding="utf-8")
    record = build_snapshot(compilation, TEXTS[2])
    for token in record["tokens"]:
        assert text[token["start"]:token["end"]] == token["raw"]

def _index():
    from stoplist_ledger import rule_index as module

    module.clear_rule_index_cache()
    return module

def test_rule_index_matches_linear_dict_on_priority_override(populated, tmp_path):
    import yaml
    from stoplist_ledger.compiler import build_compilation

    declaration = {
        "name": "override-index", "scope": "*",
        "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
        "tables": [{"id": "conflict_stop", "version": 1},
                   {"id": "broken_synonym", "version": 1}],
        "priority": ["conflict_stop", "broken_synonym"],
    }
    path = tmp_path / "override.yaml"
    path.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    compilation = build_compilation(populated, path)
    linear = {rule["term"]: rule for rule in compilation["rules"]}
    index = _index().RuleIndex(compilation["rules"])
    for term in linear:
        assert index.lookup(term) is linear[term]
    assert index.lookup("absent-term") is None
    winner = index.lookup("alpha")
    assert winner["disposition"] == "drop"
    assert winner["overridden_by"][0]["disposition"] == "merge"
    assert index.bucket_size("alpha") == sum(
        1 for term in linear if len(term) == len("alpha"))

def test_rule_index_buckets_and_lookups():
    # Length buckets narrow prefix candidates before exact term lookup, so a
    # prefix scan never touches terms that cannot possibly match its length.
    index = _index().RuleIndex([{"term": "ab"}, {"term": "cd"},
                                {"term": "abc"}, {"term": "de"}])
    assert index.bucket_count == 2 and index.term_count == 4
    assert index.prefix_candidates("a") == ["ab", "abc"]
    assert index.prefix_candidates("ab") == ["ab", "abc"]
    assert index.lookup("ab")["term"] == "ab" and index.lookup("zz") is None

def test_rule_index_cache_is_keyed_and_pipeline_safe(populated, clock):
    module = _index()
    a = compile_fixture(populated, clock, "pipeline_a.yaml")
    b = compile_fixture(populated, clock, "pipeline_b.yaml")
    index_a, reused = module.get_rule_index(a)
    assert reused is False
    index_b, _ = module.get_rule_index(b)
    assert a["rules_hash"] != b["rules_hash"] and index_a is not index_b
    assert index_a.lookup("gamma") is None
    assert index_b.lookup("gamma")["disposition"] == "drop"
    rolled_back, reused_again = module.get_rule_index(a)
    assert reused_again is True and rolled_back is index_a
    stripped = {key: value for key, value in a.items() if key != "rules_hash"}
    fallback, reused_fallback = module.get_rule_index(stripped)
    again = module.get_rule_index(dict(stripped))
    assert reused_fallback is False and again[1] is True
    assert fallback is again[0] and fallback.lookup("gamma") is None

def test_snapshot_results_match_with_and_without_index(populated, clock,
                                                       monkeypatch):
    compilation = compile_fixture(populated, clock)
    baseline = build_snapshot(compilation, TEXTS[0])

    class LinearOnly:
        term_count = bucket_count = 0

        def lookup(self, term):
            return {rule["term"]: rule for rule in compilation["rules"]}.get(term)

        def bucket_size(self, term):
            return 0

    monkeypatch.setattr("stoplist_ledger.snapshot.get_rule_index",
                        lambda comp: (LinearOnly(), True))
    linear = build_snapshot(compilation, TEXTS[0])
    assert linear["result_hash"] == baseline["result_hash"]
    assert linear["tokens"] == baseline["tokens"]

def test_lookup_stats_for_large_texts_only(populated, clock, tmp_path):
    from stoplist_ledger.util import stable_hash

    _index()
    compilation = compile_fixture(populated, clock)
    small = build_snapshot(compilation, TEXTS[0])
    assert "lookup_stats" not in small
    semantic = {key: value for key, value in small.items()
                if key not in {"lookup_stats", "result_hash"}}
    assert stable_hash(semantic) == small["result_hash"]
    big = tmp_path / "big.txt"
    big.write_text(" ".join(["alpha"] * 200), encoding="utf-8")
    large = build_snapshot(compilation, big)
    assert large["result_hash"] == build_snapshot(compilation, big)["result_hash"]
    stats = large["lookup_stats"]
    assert stats["token_count"] == stats["lookups"] == 200
    assert stats["hits"] == 200 and stats["misses"] == 0
    assert stats["index_reused"] is True
    assert stats["index_term_count"] == compilation["rule_count"]
    assert stats["candidate_bucket_max"] >= 1
    assert set(stats) == {
        "token_count", "lookups", "hits", "misses", "index_reused",
        "index_term_count", "index_bucket_count", "candidate_bucket_max",
        "candidate_bucket_total"}

def test_snapshot_repeat_across_pipelines_stays_hash_stable(populated, clock):
    # Swapping pipelines repeatedly must not let one index leak into another.
    def hashes():
        return [build_snapshot(compile_fixture(populated, clock, name),
                               text)["result_hash"]
                for name in ("pipeline_a.yaml", "pipeline_b.yaml")
                for text in TEXTS]

    assert hashes() == hashes()
