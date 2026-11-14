"""Compile-time merge-chain closure: endpoints, audit trail, failures."""

from __future__ import annotations

import pytest
import yaml

from conftest import compile_fixture, register_table
from stoplist_ledger.compiler import build_compilation
from stoplist_ledger.errors import LedgerError


def _write_decl(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _register_chain_tables(tmp_path, populated, clock) -> None:
    """gray -> grey in a high-priority synonym, grey -> color in a lower one."""

    high = tmp_path / "chain_hi_v1.yaml"
    high.write_text("gray: grey\n", encoding="utf-8")
    low = tmp_path / "chain_lo_v1.yaml"
    low.write_text("grey: color\n", encoding="utf-8")
    register_table(populated, clock, high)
    register_table(populated, clock, low)


def _chain_declaration(tmp_path, name, merge_targets):
    return _write_decl(
        tmp_path,
        f"{name}.yaml",
        {
            "name": name,
            "scope": "*",
            "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
            "tables": [
                {"id": "chain_hi", "version": 1, "scopes": ["*"]},
                {"id": "chain_lo", "version": 1, "scopes": ["*"]},
            ],
            "priority": ["chain_hi", "chain_lo"],
            "merge_targets": list(merge_targets),
        },
    )


def test_merge_chain_resolves_to_final_endpoint(tmp_path, populated, clock) -> None:
    _register_chain_tables(tmp_path, populated, clock)
    compilation = build_compilation(
        populated, _chain_declaration(tmp_path, "chain", ("color",))
    )
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    assert rules["gray"]["target"] == "color"
    assert rules["grey"]["target"] == "color"
    chain = rules["gray"]["merge_chain"]
    assert [step["term"] for step in chain] == ["gray", "grey"]
    assert [step["target"] for step in chain] == ["grey", "color"]
    assert [step["source"]["table_id"] for step in chain] == [
        "chain_hi", "chain_lo"
    ]
    assert rules["grey"]["merge_chain"][0]["source"]["table_id"] == "chain_lo"


def test_merge_allow_list_applies_to_final_endpoint(tmp_path, populated,
                                                    clock) -> None:
    _register_chain_tables(tmp_path, populated, clock)
    with pytest.raises(LedgerError) as caught:
        build_compilation(
            populated, _chain_declaration(tmp_path, "narrow", ("grey",))
        )
    assert caught.value.code == "E_MERGE_TARGET_NOT_ALLOWED"
    assert caught.value.details["target"] == "color"
    assert caught.value.details["term"] == "gray"


def test_merge_endpoint_dropped_by_higher_priority_fails(tmp_path, populated,
                                                         clock) -> None:
    stop = tmp_path / "chain_stop_v1.txt"
    stop.write_text("grey\n", encoding="utf-8")
    register_table(populated, clock, stop)
    synonym = tmp_path / "chain_syn_v1.yaml"
    synonym.write_text("gray: grey\n", encoding="utf-8")
    register_table(populated, clock, synonym)
    path = _write_decl(
        tmp_path,
        "dropped.yaml",
        {
            "name": "dropped-endpoint",
            "scope": "*",
            "tables": [
                {"id": "chain_stop", "version": 1, "scopes": ["*"]},
                {"id": "chain_syn", "version": 1, "scopes": ["*"]},
            ],
            "priority": ["chain_stop", "chain_syn"],
        },
    )
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_MERGE_TARGET_DROPPED"
    details = caught.value.details
    assert details["term"] == "gray"
    assert details["target"] == "grey"
    assert details["dropped_by"]["table_id"] == "chain_stop"
    assert [step["target"] for step in details["chain"]] == ["grey"]


def test_merge_cycle_fails_with_loop_details(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "cycle.yaml",
        {
            "name": "cycle",
            "scope": "*",
            "tables": [{"id": "broken_synonym", "version": 1, "scopes": ["*"]}],
            "priority": ["broken_synonym"],
            "merge_targets": ["alphacore", "missingtarget"],
        },
    )
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_MERGE_CYCLE"
    assert caught.value.details["cycle"] == ["xcycle", "ycycle", "xcycle"]
    assert caught.value.details["term"] == "xcycle"


def test_merge_missing_target_fails_without_allowlist(tmp_path, populated) -> None:
    path = _write_decl(
        tmp_path,
        "missing-target.yaml",
        {
            "name": "missing-target",
            "scope": "*",
            "tables": [{"id": "synonym", "version": 1, "scopes": ["*"]}],
            "priority": ["synonym"],
        },
    )
    with pytest.raises(LedgerError) as caught:
        build_compilation(populated, path)
    assert caught.value.code == "E_MERGE_TARGET_MISSING"
    assert caught.value.details["target"] == "color"


def test_rules_hash_tracks_closure_result(tmp_path, populated, clock) -> None:
    synonym = tmp_path / "chain_syn_v1.yaml"
    synonym.write_text("gray: grey\n", encoding="utf-8")
    register_table(populated, clock, synonym)
    for version, target in ((1, "color"), (2, "silver")):
        downstream = tmp_path / f"chain_down_v{version}.yaml"
        downstream.write_text(f"grey: {target}\n", encoding="utf-8")
        register_table(populated, clock, downstream)

    def build(version: int) -> dict:
        return build_compilation(
            populated,
            _write_decl(
                tmp_path,
                f"closure-v{version}.yaml",
                {
                    "name": f"closure-v{version}",
                    "scope": "*",
                    "tables": [
                        {"id": "chain_syn", "version": 1, "scopes": ["*"]},
                        {"id": "chain_down", "version": version, "scopes": ["*"]},
                    ],
                    "priority": ["chain_syn", "chain_down"],
                    "merge_targets": ["color", "silver"],
                },
            ),
        )

    first, second = build(1), build(2)
    first_rules = {rule["term"]: rule for rule in first["rules"]}
    second_rules = {rule["term"]: rule for rule in second["rules"]}
    assert first_rules["gray"]["target"] == "color"
    assert second_rules["gray"]["target"] == "silver"
    assert first["rules_hash"] != second["rules_hash"]


def test_single_hop_merge_records_one_step_chain(populated, clock) -> None:
    compilation = compile_fixture(populated, clock)
    rules = {rule["term"]: rule for rule in compilation["rules"]}
    gray = rules["gray"]
    assert gray["target"] == "grey"
    assert len(gray["merge_chain"]) == 1
    step = gray["merge_chain"][0]
    assert (step["term"], step["target"]) == ("gray", "grey")
    assert step["source"]["table_id"] == "synonym"
