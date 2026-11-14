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
