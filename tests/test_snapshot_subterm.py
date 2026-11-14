"""Subterm (substring) attribution tests for build_snapshot."""

from __future__ import annotations

from pathlib import Path

import yaml

from stoplist_ledger.compiler import compile_declaration
from stoplist_ledger.registry import register_file
from stoplist_ledger.snapshot import build_snapshot


def _subterm_snapshot(populated, clock, tmp_path: Path, terms: list[str],
                      text: str, name: str = "sub", version: int = 1):
    table = tmp_path / f"{name}_v{version}.txt"
    table.write_text("\n".join(terms) + "\n", encoding="utf-8")
    register_file(populated, table, clock, "op-test-01")
    declaration = tmp_path / f"{name}.yaml"
    declaration.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "scope": "*",
                "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
                "tables": [{"id": name, "version": version}],
            }
        ),
        encoding="utf-8",
    )
    compilation = compile_declaration(populated, declaration, clock, "op-test-01")
    path = tmp_path / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    return build_snapshot(compilation, path), compilation


def _spans(token: dict) -> list[tuple[str, int, int]]:
    return [
        (hit["term"], hit["span_start"], hit["span_end"])
        for hit in token["attribution"]["candidates"]
    ]


def test_subterm_multi_candidate_arbitration(populated, clock, tmp_path) -> None:
    record, _ = _subterm_snapshot(
        populated, clock, tmp_path, ["alpha", "not"], "notalpha gamma"
    )
    token = record["tokens"][0]
    assert token["raw"] == "notalpha"
    assert token["disposition"] == "keep"
    assert token["source"]["rule"] == "default:keep"
    assert _spans(token) == [("alpha", 3, 8), ("not", 0, 3)]
    candidates = token["attribution"]["candidates"]
    assert candidates[0]["disposition"] == "drop"
    assert candidates[0]["source"]["table_id"] == "sub"
    assert token["attribution"]["winner"] == candidates[0]
    assert token["attribution"]["winner_reason"] == "longest_term"
    assert record["tokens"][1]["attribution"] == {}
    assert record["summary"]["subterm_hit"] == 1
    assert record["summary"]["subterm_only_affected"] == 1


def test_subterm_equal_length_prefers_leftmost(populated, clock, tmp_path) -> None:
    record, _ = _subterm_snapshot(
        populated, clock, tmp_path, ["gray", "grey"], "graygrey greygray",
        name="eq",
    )
    first, second = record["tokens"]
    assert _spans(first) == [("gray", 0, 4), ("grey", 4, 8)]
    assert first["attribution"]["winner"]["term"] == "gray"
    assert first["attribution"]["winner_reason"] == "leftmost"
    assert _spans(second) == [("grey", 0, 4), ("gray", 4, 8)]
    assert second["attribution"]["winner"]["term"] == "grey"
    assert second["attribution"]["winner_reason"] == "leftmost"


def test_subterm_longest_term_beats_position(populated, clock, tmp_path) -> None:
    record, _ = _subterm_snapshot(
        populated, clock, tmp_path, ["color", "grey"], "colorgrey", name="mix"
    )
    token = record["tokens"][0]
    assert _spans(token) == [("color", 0, 5), ("grey", 5, 9)]
    assert token["attribution"]["winner"]["term"] == "color"
    assert token["attribution"]["winner_reason"] == "longest_term"


def test_subterm_winner_stable_across_rules_hash(populated, clock, tmp_path) -> None:
    before, comp_a = _subterm_snapshot(
        populated, clock, tmp_path, ["alpha", "not"], "notalpha", name="stab"
    )
    after, comp_b = _subterm_snapshot(
        populated, clock, tmp_path, ["alpha", "not", "unrelated"], "notalpha",
        name="stab", version=2,
    )
    assert comp_a["rules_hash"] != comp_b["rules_hash"]
    winner_a = before["tokens"][0]["attribution"]
    winner_b = after["tokens"][0]["attribution"]
    assert winner_a["winner"]["term"] == winner_b["winner"]["term"] == "alpha"
    assert winner_a["winner_reason"] == winner_b["winner_reason"] == "longest_term"
    assert _spans(before["tokens"][0]) == _spans(after["tokens"][0])


def test_subterm_spans_use_normalized_coordinates(populated, clock, tmp_path) -> None:
    record, _ = _subterm_snapshot(
        populated, clock, tmp_path, ["alpha", "not"], "ＡＬＰＨＡ ﬀnot",
        name="nfkc",
    )
    fullwidth, ligature = record["tokens"]
    assert fullwidth["raw"] == "ＡＬＰＨＡ"
    assert fullwidth["term"] == "alpha"
    assert fullwidth["disposition"] == "drop"
    assert _spans(fullwidth) == [("alpha", 0, 5)]
    assert ligature["raw"] == "ﬀnot"
    assert ligature["term"] == "ffnot"
    assert ligature["disposition"] == "keep"
    assert _spans(ligature) == [("not", 2, 5)]
    assert ligature["attribution"]["winner_reason"] == "only_candidate"
