from __future__ import annotations

from pathlib import Path

import pytest

from conftest import compile_fixture
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.snapshot import (
    SEGMENT_TOKEN_KEYS,
    build_snapshot,
    snapshot_text,
    tokenize_segments,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_tokenize_segments_boundary_attribution() -> None:
    # A token belongs to the segment holding its start offset; spilling
    # past that segment's end flags it. No space is synthesized at joins.
    tokens = tokenize_segments("alpha beta", [("one", 0, 3), ("two", 3, 10)])
    assert [t["segment_id"] for t in tokens] == ["one", "two"]
    assert [t["crosses_boundary"] for t in tokens] == [True, False]
    # A token starting exactly on a boundary belongs to the later segment.
    tokens = tokenize_segments("alpha beta", [("one", 0, 6), ("two", 6, 10)])
    assert [t["segment_id"] for t in tokens] == ["one", "two"]
    assert [t["crosses_boundary"] for t in tokens] == [False, False]


def test_segmented_snapshot_trace_and_provenance(populated, clock,
                                                 tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    first = _write(tmp_path, "first.txt", "alp")
    second = _write(tmp_path, "second.txt", "ha beta")
    record = snapshot_text(populated, compilation["id"], clock=clock,
                           segments=[("one", first), ("two", second)])
    tokens = record["tokens"]
    assert [t["raw"] for t in tokens] == ["alpha", "beta"]  # join adds no space
    assert (tokens[0]["start"], tokens[0]["end"]) == (0, 5)  # global offsets
    assert (tokens[0]["segment_id"], tokens[0]["crosses_boundary"]) == (
        "one", True)
    assert (tokens[1]["segment_id"], tokens[1]["crosses_boundary"]) == (
        "two", False)
    assert record["text_id"] == "one+two"  # derived from segment names
    assert [(s["id"], s["start"], s["end"]) for s in record["segments"]] == [
        ("one", 0, 3), ("two", 3, 10)]
    assert all(len(s["sha256"]) == 64 and s["source_path"]
               for s in record["segments"])


def test_segmented_hash_ignores_segmentation(populated, clock, tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    a1 = _write(tmp_path, "a1.txt", "Alpha be")
    a2 = _write(tmp_path, "a2.txt", "ta gamma")
    b1 = _write(tmp_path, "b1.txt", "Alpha beta ga")
    b2 = _write(tmp_path, "b2.txt", "mma")
    whole = _write(tmp_path, "whole.txt", "Alpha beta gamma")
    seg_a = build_snapshot(compilation, text_id="joined",
                           segments=[{"id": "x#p1", "path": a1},
                                     {"id": "x#p2", "path": a2}])
    seg_b = build_snapshot(compilation, text_id="joined",
                           segments=[{"id": "y#p1", "path": b1},
                                     {"id": "y#p2", "path": b2}])
    legacy = build_snapshot(compilation, whole, text_id="joined")
    # Same concatenated string, different cuts — even vs the single-file path.
    assert seg_a["result_hash"] == seg_b["result_hash"] == legacy["result_hash"]
    assert seg_a["text_hash"] == seg_b["text_hash"] == legacy["text_hash"]
    # The attribution differs, so the hash provably ignores it.
    assert [t["segment_id"] for t in seg_a["tokens"]] != [
        t["segment_id"] for t in seg_b["tokens"]]
    stripped = [{k: v for k, v in t.items() if k not in SEGMENT_TOKEN_KEYS}
                for t in seg_a["tokens"]]
    assert stripped == legacy["tokens"]


def test_segmented_snapshot_dedupes(populated, clock, tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    a1 = _write(tmp_path, "a1.txt", "Alpha be")
    a2 = _write(tmp_path, "a2.txt", "ta gamma")
    b1 = _write(tmp_path, "b1.txt", "Alpha beta ")
    b2 = _write(tmp_path, "b2.txt", "gamma")
    first = snapshot_text(populated, compilation["id"], clock=clock,
                          segments=[("x#p1", a1), ("x#p2", a2)],
                          text_id="joined")
    second = snapshot_text(populated, compilation["id"], clock=clock,
                           segments=[("y#p1", b1), ("y#p2", b2)],
                           text_id="joined")
    assert first["result_hash"] == second["result_hash"]
    assert first["id"] == second["id"]
    assert len(populated.list_snapshots(compilation["id"])) == 1
    # The persisted row keeps the first segmentation's provenance.
    assert [s["id"] for s in second["segments"]] == ["x#p1", "x#p2"]
    # Same segment ids with a different split derive the same text_id.
    third = snapshot_text(populated, compilation["id"], clock=clock,
                          segments=[("x#p1", b1), ("x#p2", b2)])
    assert third["text_id"] == "x#p1+x#p2"
    assert len(populated.list_snapshots(compilation["id"])) == 2


def test_segmented_snapshot_input_validation(populated, clock, tmp_path) -> None:
    compilation = compile_fixture(populated, clock)
    path = _write(tmp_path, "a.txt", "alpha")
    bad_calls = [
        lambda: build_snapshot(compilation, path, segments=[("a", path)]),
        lambda: build_snapshot(compilation),
        lambda: build_snapshot(compilation, segments=[]),
        lambda: build_snapshot(compilation,
                               segments=[("a", path), ("a", path)]),
        lambda: build_snapshot(compilation, segments=[("a", path)],
                               text_id=""),
    ]
    for call in bad_calls:
        with pytest.raises(LedgerError) as caught:
            call()
        assert caught.value.code == "E_BAD_FORMAT"
    with pytest.raises(LedgerError) as caught:
        build_snapshot(compilation, segments=[("a", tmp_path / "no.txt")])
    assert caught.value.code == "E_IO"
