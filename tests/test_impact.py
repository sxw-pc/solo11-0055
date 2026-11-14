from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FIXTURES, TEXTS, compile_fixture, snapshot_set
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.impact import (
    build_impact_payload,
    compare_rule_sets,
    create_impact,
)
from stoplist_ledger.report import write_report


def _ab(populated, clock):
    a = compile_fixture(populated, clock, "pipeline_a.yaml")
    snaps_a = snapshot_set(populated, clock, a)
    b = compile_fixture(populated, clock, "pipeline_b.yaml")
    snaps_b = snapshot_set(populated, clock, b)
    return a, snaps_a, b, snaps_b


def test_impact_matches_golden(populated, clock) -> None:
    a, snaps_a, b, snaps_b = _ab(populated, clock)
    payload = build_impact_payload(
        populated,
        [snapshot["id"] for snapshot in snaps_a],
        [snapshot["id"] for snapshot in snaps_b],
        clock.timestamp(),
    )
    expected = json.loads(
        (FIXTURES / "expected" / "impact_ab.json").read_text(encoding="utf-8")
    )
    assert payload["summary"] == expected["summary"]
    assert payload["rule_changes"] == expected["rule_changes"]
    assert payload["text_changes"] == expected["text_changes"]
    assert payload["left"]["compilation"]["name"] == a["name"]
    assert payload["right"]["compilation"]["name"] == b["name"]


def test_impact_report_persistence_and_markdown(populated, clock, tmp_path) -> None:
    a, snaps_a, b, snaps_b = _ab(populated, clock)
    first = create_impact(
        populated, [item["id"] for item in snaps_a],
        [item["id"] for item in snaps_b], clock,
    )
    second = create_impact(
        populated, [item["id"] for item in snaps_a],
        [item["id"] for item in snaps_b], clock,
    )
    assert first["id"] == second["id"]
    assert first["report_hash"] == second["report_hash"]
    assert len(populated.list_impacts()) == 1
    json_path = write_report(first, "json", tmp_path / "impact.json")
    md_path = write_report(first, "md", tmp_path / "impact.md")
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["summary"]["new_dropped_terms"] == 1
    markdown = md_path.read_text(encoding="utf-8")
    assert "Merge target changes" in markdown
    assert "gray" in markdown
    assert "sample_03" in markdown


def test_rule_comparison_is_sorted() -> None:
    left = [
        {"term": "b", "disposition": "drop", "target": None},
        {"term": "a", "disposition": "merge", "target": "x"},
    ]
    right = [
        {"term": "c", "disposition": "drop", "target": None},
        {"term": "b", "disposition": "keep", "target": None},
    ]
    changes = compare_rule_sets(left, right)
    assert [item["term"] for item in changes["new_dropped"]] == ["c"]
    assert [item["term"] for item in changes["gone_dropped"]] == ["b"]
    assert [item["term"] for item in changes["merge_target_changes"]] == ["a"]


def test_impact_requires_snapshots(populated, clock) -> None:
    with pytest.raises(LedgerError) as caught:
        build_impact_payload(populated, [], [], clock.timestamp())
    assert caught.value.code == "E_BAD_FORMAT"


def test_impact_missing_snapshot(populated, clock) -> None:
    with pytest.raises(LedgerError) as caught:
        build_impact_payload(populated, [999], [998], clock.timestamp())
    assert caught.value.code == "E_REF_NOT_FOUND"


def test_impact_sides_must_use_one_compilation(populated, clock) -> None:
    a, snaps_a, b, snaps_b = _ab(populated, clock)
    with pytest.raises(LedgerError) as caught:
        build_impact_payload(
            populated,
            [snaps_a[0]["id"], snaps_b[0]["id"]],
            [snaps_b[1]["id"]],
            clock.timestamp(),
        )
    assert caught.value.code == "E_BAD_FORMAT"


def test_compare_rule_sets_reports_exemptions_both_directions() -> None:
    left = [
        {"term": "alpha", "disposition": "whitelist_exempt", "target": None},
        {"term": "beta", "disposition": "drop", "target": None},
    ]
    right = [
        {"term": "alpha", "disposition": "drop", "target": None},
        {"term": "beta", "disposition": "whitelist_exempt", "target": None},
    ]
    changes = compare_rule_sets(left, right)
    assert [item["term"] for item in changes["exemption_changes"]] == [
        "alpha", "beta"
    ]
    assert [item["term"] for item in changes["new_dropped"]] == ["alpha"]
    assert [item["term"] for item in changes["gone_dropped"]] == ["beta"]


def test_compare_rule_sets_merge_transition() -> None:
    left = [{"term": "x", "disposition": "merge", "target": "one"}]
    right = [{"term": "x", "disposition": "drop", "target": None}]
    changes = compare_rule_sets(left, right)
    assert changes["merge_target_changes"] == [{
        "term": "x",
        "before": {"disposition": "merge", "target": "one"},
        "after": {"disposition": "drop", "target": None},
    }]


def test_compare_rule_sets_input_order_does_not_matter() -> None:
    left = [
        {"term": "z", "disposition": "drop", "target": None},
        {"term": "a", "disposition": "drop", "target": None},
    ]
    right = []
    first = compare_rule_sets(left, right)
    second = compare_rule_sets(list(reversed(left)), right)
    assert first == second
    assert [item["term"] for item in first["gone_dropped"]] == ["a", "z"]


def test_compare_texts_detects_token_changes() -> None:
    from stoplist_ledger.impact import compare_texts

    left = {
        "text_id": "t", "text_hash": "a", "result_hash": "r1",
        "tokens": [
            {"index": 0, "raw": "alpha", "term": "alpha",
             "disposition": "drop", "target": None},
            {"index": 1, "raw": "beta", "term": "beta",
             "disposition": "keep", "target": None},
        ],
    }
    right = {
        "text_id": "t", "text_hash": "a", "result_hash": "r2",
        "tokens": [
            {"index": 0, "raw": "alpha", "term": "alpha",
             "disposition": "keep", "target": None},
            {"index": 1, "raw": "beta", "term": "beta",
             "disposition": "keep", "target": None},
        ],
    }
    diff = compare_texts(left, right)
    assert diff["affected"] is True
    assert diff["affected_tokens"] == 1
    assert diff["changes"][0]["raw"] == "alpha"
    assert diff["changes"][0]["before"]["disposition"] == "drop"


def test_impact_audit_event(populated, clock) -> None:
    a, snaps_a, b, snaps_b = _ab(populated, clock)
    create_impact(populated, [item["id"] for item in snaps_a],
                  [item["id"] for item in snaps_b], clock)
    assert "impact.create" in [
        event["event"] for event in populated.list_audit()
    ]


def test_impact_json_has_no_absolute_source_paths(populated, clock) -> None:
    a, snaps_a, b, snaps_b = _ab(populated, clock)
    payload = build_impact_payload(
        populated, [item["id"] for item in snaps_a],
        [item["id"] for item in snaps_b], clock.timestamp(),
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "D:\\\\" not in serialized
    assert "/home/" not in serialized


def test_segmented_snapshot_aligns_with_legacy_in_impact(populated, clock,
                                                         tmp_path) -> None:
    from stoplist_ledger.snapshot import snapshot_text

    a = compile_fixture(populated, clock, "pipeline_a.yaml")
    b = compile_fixture(populated, clock, "pipeline_b.yaml")
    text = TEXTS[0].read_text(encoding="utf-8")
    cut = 8  # splits the token "beta" across the two segment files
    first = tmp_path / "sample_01#p1.txt"
    first.write_text(text[:cut], encoding="utf-8")
    second = tmp_path / "sample_01#p2.txt"
    second.write_text(text[cut:], encoding="utf-8")
    segmented = snapshot_text(
        populated, a["id"], clock=clock,
        segments=[("sample_01#p1", first), ("sample_01#p2", second)],
        text_id="sample_01",
    )
    legacy = snapshot_text(populated, b["id"], TEXTS[0], clock)
    payload = build_impact_payload(
        populated, [segmented["id"]], [legacy["id"]], clock.timestamp()
    )
    expected = json.loads(
        (FIXTURES / "expected" / "impact_ab.json").read_text(encoding="utf-8")
    )
    golden = [item for item in expected["text_changes"]
              if item["text_id"] == "sample_01"]
    # A segmented text aligns by text_id and diffs exactly like the
    # single-file snapshot of the same string.
    assert payload["text_changes"] == golden
