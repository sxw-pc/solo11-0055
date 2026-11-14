from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FIXTURES
from stoplist_ledger.errors import LedgerError
from stoplist_ledger.normalizer import NormalizationPolicy, assert_idempotent
from stoplist_ledger.registry import register_file

STOPLISTS = FIXTURES / "stoplists"


def test_register_two_txt_versions(store, clock) -> None:
    first = register_file(store, STOPLISTS / "zh_stop_v1.txt", clock,
                          operator="op-test-01")
    second = register_file(store, STOPLISTS / "zh_stop_v2.txt", clock,
                           operator="op-test-01")
    assert first["table_id"] == "zh_stop"
    assert (first["version"], second["version"]) == (1, 2)
    assert (first["comment_count"], first["entry_count"]) == (1, 6)
    assert second["entry_count"] == 7
    assert (second["diff"]["base_version"], second["diff"]["added"],
            second["diff"]["removed"], second["diff"]["modified_count"]) == (
        1, [8], [], 0)
    assert len(store.list_versions("zh_stop")) == 2
    assert store.summary()["table_versions"] == 2


def test_register_synonym_and_metadata(store, clock) -> None:
    record = register_file(store, STOPLISTS / "synonym_v1.yaml", clock)
    assert record["kind"] == "synonym"
    assert record["term_count"] == 2
    assert (record["entries"][0]["raw"], record["entries"][0]["target"]) == (
        "colour", "color")
    assert (record["line_count"], record["comment_count"]) == (3, 1)


def test_duplicate_content_is_rejected(store, clock) -> None:
    path = STOPLISTS / "zh_stop_v1.txt"
    register_file(store, path, clock)
    with pytest.raises(LedgerError) as caught:
        register_file(store, path, clock)
    assert caught.value.code == "E_DUP_CONTENT"


def test_same_slot_new_content_is_rejected(tmp_path: Path, store, clock) -> None:
    (tmp_path / "words_v1.txt").write_text("alpha\n", encoding="utf-8")
    second = tmp_path / "words_v2.txt"
    second.write_text("beta\n", encoding="utf-8")
    register_file(store, tmp_path / "words_v1.txt", clock)
    with pytest.raises(LedgerError) as caught:
        register_file(store, second, clock, version=1)
    assert caught.value.code == "E_DUP_VERSION"


@pytest.mark.parametrize(("content", "code"), [
    ("\u0001bad\n", "E_CTRL_CHAR"),
    ("x" * 257 + "\n", "E_TERM_TOO_LONG")])
def test_invalid_txt_terms(tmp_path: Path, store, clock, content, code) -> None:
    path = tmp_path / "invalid_v1.txt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        register_file(store, path, clock)
    assert caught.value.code == code


def test_empty_yaml_target_is_rejected(tmp_path: Path, store, clock) -> None:
    (tmp_path / "badmap_v1.yaml").write_text('source: ""\n', encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        register_file(store, tmp_path / "badmap_v1.yaml", clock)
    assert caught.value.code == "E_EMPTY_TERM"


def test_modified_line_diff(tmp_path: Path, store, clock) -> None:
    (tmp_path / "custom_v1.txt").write_text("alpha\nbeta\ngamma\n",
                                            encoding="utf-8")
    two = tmp_path / "custom_v2.txt"
    two.write_text("alpha\nBETA\ngamma\n", encoding="utf-8")
    register_file(store, tmp_path / "custom_v1.txt", clock)
    record = register_file(
        store, two, clock,
        policy=NormalizationPolicy(case="case_sensitive", whitespace="trim"))
    assert record["diff"]["modified"] == [{"old": 2, "new": 2}]
    assert record["diff"]["modified_count"] == 1


def test_normalization_is_idempotent() -> None:
    for policy in (NormalizationPolicy("case_sensitive", "trim"),
                   NormalizationPolicy("lower", "collapse"),
                   NormalizationPolicy("nfkc_lower", "collapse")):
        assert assert_idempotent(policy, "  Mixed　CASE  ") == policy.normalize(
            "  Mixed　CASE  ")


def test_normalization_rejects_bad_policy() -> None:
    with pytest.raises(LedgerError) as caught:
        NormalizationPolicy(case="upper")
    assert caught.value.code == "E_BAD_POLICY"


def test_filename_identity_inference(store, clock, tmp_path: Path) -> None:
    path = tmp_path / "extra_noise_v4.txt"
    path.write_text("promo_y\n", encoding="utf-8")
    record = register_file(store, path, clock)
    assert (record["table_id"], record["version"], record["kind"]) == (
        "extra_noise", 4, "noise")


def test_duplicate_synonym_content_across_ids_rejected(tmp_path, store, clock):
    for name in ("map_one_v1.yaml", "map_two_v1.yaml"):
        (tmp_path / name).write_text("colour: color\n", encoding="utf-8")
    register_file(store, tmp_path / "map_one_v1.yaml", clock)
    with pytest.raises(LedgerError) as caught:
        register_file(store, tmp_path / "map_two_v1.yaml", clock)
    assert caught.value.code == "E_DUP_CONTENT"


def test_comments_and_blank_lines_are_not_entries(tmp_path, store, clock) -> None:
    path = tmp_path / "commented_v1.txt"
    path.write_text("# heading\n\n  # indented comment\nalpha\n\n# tail\n",
                    encoding="utf-8")
    record = register_file(store, path, clock)
    assert [entry["raw"] for entry in record["entries"]] == ["alpha"]
    assert (record["comment_count"], record["line_count"],
            record["entry_count"]) == (3, 6, 1)


def test_yaml_root_must_be_mapping(tmp_path, store, clock) -> None:
    (tmp_path / "list_v1.yaml").write_text("- alpha\n- beta\n", encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        register_file(store, tmp_path / "list_v1.yaml", clock)
    assert caught.value.code == "E_BAD_FORMAT"


def test_yaml_values_must_be_strings(tmp_path, store, clock) -> None:
    (tmp_path / "numbers_v1.yaml").write_text("alpha: 7\n", encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        register_file(store, tmp_path / "numbers_v1.yaml", clock)
    assert caught.value.code == "E_BAD_FORMAT"


def test_allow_existing_returns_original_version(store, clock) -> None:
    path = STOPLISTS / "zh_stop_v1.txt"
    first = register_file(store, path, clock)
    second = register_file(store, path, clock, allow_existing=True)
    assert first["id"] == second["id"]
    assert len(store.list_versions("zh_stop")) == 1


def test_auto_increment_version_when_explicit_id(tmp_path, store, clock) -> None:
    (tmp_path / "manual_one.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "manual_two.txt").write_text("beta\n", encoding="utf-8")
    one = register_file(store, tmp_path / "manual_one.txt", clock,
                        table_id="manual")
    two = register_file(store, tmp_path / "manual_two.txt", clock,
                        table_id="manual")
    assert (one["version"], two["version"]) == (1, 2)


def test_list_tables_aggregates_versions(populated) -> None:
    summary = {row["table_id"]: row for row in populated.list_tables()}
    assert (summary["zh_stop"]["latest_version"],
            summary["zh_stop"]["version_count"]) == (3, 3)
    assert summary["synonym"]["latest_version"] == 2
    assert summary["whitelist"]["kind"] == "whitelist"


def test_audit_ledger_records_registration(store, clock) -> None:
    register_file(store, STOPLISTS / "noise_v1.txt", clock, operator="op-audit")
    event = store.list_audit()[-1]
    assert (event["event"], event["operator"], event["subject"]) == (
        "table.register", "op-audit", "noise@v1")
    assert event["detail"]["entry_count"] == 2


def test_policy_changes_content_hash(tmp_path, store, clock) -> None:
    path = tmp_path / "mixed_v1.txt"
    path.write_text("Alpha\n", encoding="utf-8")
    lower = register_file(store, path, clock, table_id="lower_table",
                          policy=NormalizationPolicy("lower", "trim"))
    strict = register_file(store, path, clock, table_id="strict_table",
                           policy=NormalizationPolicy("case_sensitive", "trim"))
    assert lower["content_hash"] != strict["content_hash"]
    assert (lower["entries"][0]["term"], strict["entries"][0]["term"]) == (
        "alpha", "Alpha")


def test_source_path_is_not_absolute(store, clock) -> None:
    record = register_file(store, STOPLISTS / "zh_stop_v1.txt", clock)
    assert not Path(record["source_path"]).is_absolute()
    assert record["source_path"].endswith("zh_stop_v1.txt")


def test_empty_comment_only_table_is_valid(store, clock) -> None:
    record = register_file(store, STOPLISTS / "empty_v1.txt", clock)
    assert (record["entry_count"], record["term_count"]) == (0, 0)


def test_explicit_unknown_kind(store, clock) -> None:
    with pytest.raises(LedgerError) as caught:
        register_file(store, STOPLISTS / "noise_v1.txt", clock, kind="other")
    assert caught.value.code == "E_BAD_FORMAT"


def test_missing_file(store, clock, tmp_path) -> None:
    with pytest.raises(LedgerError) as caught:
        register_file(store, tmp_path / "missing_v1.txt", clock)
    assert caught.value.code == "E_IO"


def test_version_diff_for_synonyms(tmp_path, store, clock) -> None:
    (tmp_path / "maptable_v1.yaml").write_text("alpha: beta\ngamma: delta\n",
                                               encoding="utf-8")
    two = tmp_path / "maptable_v2.yaml"
    two.write_text("alpha: beta\nepsilon: delta\n", encoding="utf-8")
    register_file(store, tmp_path / "maptable_v1.yaml", clock)
    record = register_file(store, two, clock)
    assert record["diff"]["modified"] == [{"old": 2, "new": 2}]
    assert (record["diff"]["added"], record["diff"]["removed"],
            record["diff"]["modified_count"]) == ([], [], 1)
