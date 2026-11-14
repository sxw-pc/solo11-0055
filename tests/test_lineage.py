from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FIXTURES
from stoplist_ledger.__main__ import main
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.lineage import fold_entry_changes, reconstruct_lineage
from stoplist_ledger.registry import register_file


def _register(store, clock, name: str):
    return register_file(store, FIXTURES / "stoplists" / name, clock)


def test_lineage_replays_zh_stop_chain(store, clock) -> None:
    for name in ("zh_stop_v1.txt", "zh_stop_v2.txt", "zh_stop_v3.txt"):
        _register(store, clock, name)
    stored = store.get_version("zh_stop", 3)
    lineage = reconstruct_lineage(store, "zh_stop", 3)
    assert lineage["kind"] == "lineage"
    assert lineage["versions_replayed"] == [1, 2, 3]
    assert [e["term"] for e in lineage["entries"]] == [
        e["term"] for e in stored["entries"]]
    by_term = {e["term"]: e for e in lineage["entries"]}
    assert by_term["的"]["introduced_version"] == 1
    assert by_term["beta"]["introduced_version"] == 2
    assert by_term["gamma"]["introduced_version"] == 3
    assert by_term["gamma"]["history"][-1]["change"] == "added"
    assert "alpha" not in by_term
    assert all(e["modified_count"] == 0 for e in lineage["entries"])


def test_lineage_synonym_retarget_chain(store, clock) -> None:
    _register(store, clock, "synonym_v1.yaml")
    _register(store, clock, "synonym_v2.yaml")
    lineage = reconstruct_lineage(store, "synonym")
    assert lineage["version"] == 2
    by_term = {e["term"]: e for e in lineage["entries"]}
    gray = by_term["gray"]
    assert gray["introduced_version"] == 1
    assert gray["modified_count"] == 1
    event = gray["history"][-1]
    assert event["change"] == "retargeted"
    assert event["from"]["target"] == "grey"
    assert event["to"]["target"] == "silver"
    assert by_term["colour"]["modified_count"] == 0


def test_pure_reorder_of_duplicates_is_not_modified(tmp_path, store, clock):
    one = tmp_path / "reorder_v1.txt"
    two = tmp_path / "reorder_v2.txt"
    one.write_text("dup\nx\ndup\n", encoding="utf-8")
    two.write_text("x\ndup\ndup\n", encoding="utf-8")
    first = register_file(store, one, clock)
    second = register_file(store, two, clock)
    assert second["diff"]["added"] or second["diff"]["removed"]
    changes = fold_entry_changes(first["entries"], second["entries"],
                                 second["diff"])
    assert changes["modified"] == []
    assert changes["added"] == [] and changes["removed"] == []
    lineage = reconstruct_lineage(store, "reorder", 2)
    assert [e["introduced_version"] for e in lineage["entries"]] == [1, 1, 1]
    assert [e["modified_count"] for e in lineage["entries"]] == [0, 0, 0]


def test_rename_chain_via_line_hint(tmp_path, store, clock):
    one = tmp_path / "words_v1.txt"
    two = tmp_path / "words_v2.txt"
    one.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    two.write_text("alpha\nbeta\ndelta\n", encoding="utf-8")
    register_file(store, one, clock)
    register_file(store, two, clock)
    lineage = reconstruct_lineage(store, "words", 2)
    by_term = {e["term"]: e for e in lineage["entries"]}
    delta = by_term["delta"]
    assert delta["introduced_version"] == 1
    assert delta["modified_count"] == 1
    event = delta["history"][-1]
    assert event["change"] == "renamed"
    assert event["from"]["term"] == "gamma"
    assert event["to"]["term"] == "delta"


def test_synonym_rename_keeps_target(tmp_path, store, clock):
    one = tmp_path / "map_v1.yaml"
    two = tmp_path / "map_v2.yaml"
    one.write_text("alpha: beta\ngamma: delta\n", encoding="utf-8")
    two.write_text("alpha: beta\nsigma: delta\n", encoding="utf-8")
    register_file(store, one, clock)
    register_file(store, two, clock)
    lineage = reconstruct_lineage(store, "map", 2)
    by_term = {e["term"]: e for e in lineage["entries"]}
    event = by_term["sigma"]["history"][-1]
    assert by_term["sigma"]["introduced_version"] == 1
    assert event["change"] == "renamed"
    assert event["from"]["term"] == "gamma"
    assert event["to"]["target"] == "delta"


def test_version_gap_is_not_reconstructable(store, clock) -> None:
    _register(store, clock, "zh_stop_v1.txt")
    _register(store, clock, "zh_stop_v3.txt")
    with pytest.raises(LedgerError) as caught:
        reconstruct_lineage(store, "zh_stop", 3)
    assert caught.value.code == "E_LINEAGE_GAP"
    assert caught.value.details["missing_versions"] == [2]
    assert reconstruct_lineage(store, "zh_stop", 1)["entry_count"] == 6


def test_unknown_table_and_version(store, clock) -> None:
    _register(store, clock, "zh_stop_v1.txt")
    for table_id, version in (("ghost", 1), ("zh_stop", 9)):
        with pytest.raises(LedgerError) as caught:
            reconstruct_lineage(store, table_id, version)
        assert caught.value.code == "E_REF_NOT_FOUND"


def test_lineage_cli(tmp_path, capsys) -> None:
    var = tmp_path / "var"
    base = ["--var-dir", str(var)]
    files = [FIXTURES / "stoplists" / f"zh_stop_v{i}.txt" for i in (1, 2, 3)]
    assert main(base + ["register", *map(str, files)]) == 0
    capsys.readouterr()
    assert main(base + ["lineage", "zh_stop@v3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == 3 and out["entry_count"] == 7
    report = json.loads(Path(out["report"]).read_text(encoding="utf-8"))
    assert report["kind"] == "lineage"
    assert report["versions_replayed"] == [1, 2, 3]


def test_lineage_cli_gap_error(tmp_path, capsys) -> None:
    var = tmp_path / "var"
    base = ["--var-dir", str(var)]
    files = [FIXTURES / "stoplists" / "zh_stop_v1.txt",
             FIXTURES / "stoplists" / "zh_stop_v3.txt"]
    assert main(base + ["register", *map(str, files)]) == 0
    capsys.readouterr()
    assert main(base + ["lineage", "zh_stop", "--version", "3"]) == 2
    assert "E_LINEAGE_GAP" in capsys.readouterr().err
