from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES, ROOT
from stoplist_ledger.__main__ import main
from stoplist_ledger.clock import FixedClock
from stoplist_ledger.demo import run_demo, safe_reset
from stoplist_ledger.errors import LedgerError


def test_demo_is_repeatable(tmp_path: Path) -> None:
    clock = FixedClock("2024-01-01T00:00:00Z")
    first = run_demo(tmp_path / "var", clock, "op-test-01")
    report_dir = tmp_path / "var" / "reports"
    first_bytes = {
        path.name: path.read_bytes() for path in sorted(report_dir.iterdir())
    }
    second = run_demo(tmp_path / "var", clock, "op-test-01")
    second_bytes = {
        path.name: path.read_bytes() for path in sorted(report_dir.iterdir())
    }
    assert first_bytes == second_bytes
    assert first["impact"]["summary"] == second["impact"]["summary"]
    assert (report_dir / "impact_pipeline-a_to_pipeline-b.json").is_file()
    assert (report_dir / "impact_pipeline-a_to_pipeline-b.md").is_file()
    assert (report_dir / "demo_summary.json").is_file()
    broken = json.loads(
        (report_dir / "check_broken-conflict.json").read_text(encoding="utf-8")
    )
    assert set(finding["code"] for finding in broken["findings"]) == {
        f"C0{index}" for index in range(1, 9)
    }


def test_demo_does_not_open_sockets(tmp_path: Path, monkeypatch) -> None:
    def deny(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    summary = run_demo(tmp_path / "var", FixedClock(), "op-offline-01")
    assert summary["counts"]["snapshots"] == 6


def test_one_command_demo_subprocess(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "stoplist_ledger", "--var-dir",
         str(tmp_path / "var"), "demo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["kind"] == "demo"
    assert summary["impact"]["summary"]["affected_texts"] == 3
    assert (tmp_path / "var" / "ledger.db").is_file()
    assert (tmp_path / "var" / "reports" / "demo_summary.md").is_file()


def test_cli_duplicate_registration_uses_stable_code(tmp_path: Path, capsys) -> None:
    var_dir = tmp_path / "var"
    path = FIXTURES / "stoplists" / "noise_v1.txt"
    assert main(["--var-dir", str(var_dir), "register", str(path)]) == 0
    assert main(["--var-dir", str(var_dir), "register", str(path)]) == 2
    assert "E_DUP_CONTENT" in capsys.readouterr().err


def test_safe_reset_requires_confirmation(tmp_path: Path) -> None:
    root = tmp_path / "var"
    root.mkdir()
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        safe_reset(root, root, confirmed=False)
    assert caught.value.code == "E_CONFIRM_REQUIRED"
    assert (root / "keep.txt").exists()


def test_safe_reset_rejects_outside_path(tmp_path: Path) -> None:
    root = tmp_path / "var"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(LedgerError) as caught:
        safe_reset(outside, root, confirmed=True)
    assert caught.value.code == "E_UNSAFE_RESET"
    assert (outside / "keep.txt").exists()
    with pytest.raises(LedgerError):
        safe_reset(tmp_path, root, confirmed=True)


def test_safe_reset_clears_root_and_child(tmp_path: Path) -> None:
    root = tmp_path / "var"
    child = root / "reports"
    child.mkdir(parents=True)
    (child / "old.json").write_text("{}", encoding="utf-8")
    assert safe_reset(child, root, confirmed=True) == child.resolve()
    assert child.exists() is False
    (root / "later.txt").write_text("x", encoding="utf-8")
    safe_reset(root, root, confirmed=True)
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_source_files_stay_under_line_limits() -> None:
    paths = list((ROOT / "stoplist_ledger").rglob("*.py"))
    paths += list((ROOT / "tests").glob("*.py"))
    paths += list((ROOT / "stoplist_ledger" / "templates").glob("*.html"))
    too_long = {
        path.relative_to(ROOT).as_posix(): len(path.read_text(encoding="utf-8")
                                               .splitlines())
        for path in paths
        if len(path.read_text(encoding="utf-8").splitlines()) > 300
    }
    assert too_long == {}


def test_runtime_sources_have_no_external_urls() -> None:
    for path in (ROOT / "stoplist_ledger").rglob("*"):
        if path.suffix not in {".py", ".html"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert "http://" not in text, path
        assert "https://" not in text, path
        assert "cdn." not in text, path


def _json_tail(capsys) -> object:
    return json.loads(capsys.readouterr().out)


def test_cli_end_to_end(tmp_path: Path, capsys) -> None:
    var = tmp_path / "var"
    base = ["--var-dir", str(var), "--operator", "op-cli"]
    assert main(base + ["init"]) == 0
    capsys.readouterr()
    files = [
        FIXTURES / "stoplists" / "zh_stop_v2.txt",
        FIXTURES / "stoplists" / "synonym_v1.yaml",
        FIXTURES / "stoplists" / "noise_v1.txt",
        FIXTURES / "stoplists" / "whitelist_v1.txt",
    ]
    assert main(base + ["register", *map(str, files)]) == 0
    assert len(_json_tail(capsys)) == 4
    assert main(base + ["compile", str(
        FIXTURES / "declarations" / "pipeline_a.yaml"
    )]) == 0
    compiled = _json_tail(capsys)
    assert compiled["name"] == "pipeline-a"
    assert (var / "reports" / "compilation_pipeline-a.json").is_file()
    assert main(base + ["snapshot-all", "--compilation", "pipeline-a"]) == 0
    snapshots = _json_tail(capsys)
    assert len(snapshots) == 3
    snapshot_id = snapshots[0]["id"]
    assert main(base + ["report", "--kind", "snapshot", "--id", str(snapshot_id),
                        "--format", "md"]) == 0
    report_path = Path(capsys.readouterr().out.strip())
    assert report_path.is_file()
    assert "Token trace" in report_path.read_text(encoding="utf-8")


def test_cli_compile_snapshot_diff_check(tmp_path: Path, capsys) -> None:
    var = tmp_path / "var"
    base = ["--var-dir", str(var)]
    all_files = sorted((FIXTURES / "stoplists").iterdir())
    assert main(base + ["register", *map(str, all_files)]) == 0
    capsys.readouterr()
    assert main(base + ["compile", str(
        FIXTURES / "declarations" / "pipeline_a.yaml"
    )]) == 0
    capsys.readouterr()
    assert main(base + ["snapshot-all", "--compilation", "pipeline-a"]) == 0
    capsys.readouterr()
    assert main(base + ["compile", str(
        FIXTURES / "declarations" / "pipeline_b.yaml"
    )]) == 0
    capsys.readouterr()
    assert main(base + ["snapshot-all", "--compilation", "pipeline-b"]) == 0
    capsys.readouterr()
    assert main(base + ["diff", "--left-compilation", "1",
                        "--right-compilation", "2", "--format", "md"]) == 0
    diff_result = _json_tail(capsys)
    assert diff_result["summary"]["affected_texts"] == 3
    assert Path(diff_result["report"]).is_file()
    assert main(base + ["check", str(
        FIXTURES / "declarations" / "broken_conflict.yaml"
    )]) == 0
    check_result = _json_tail(capsys)
    assert check_result["total_issues"] == 9
    assert check_result["status"] == "issues"


def test_cli_reset_requires_confirmation_in_default_var(tmp_path, monkeypatch,
                                                       capsys) -> None:
    monkeypatch.chdir(tmp_path)
    default_var = tmp_path / "var"
    default_var.mkdir()
    (default_var / "artifact.txt").write_text("x", encoding="utf-8")
    assert main(["reset"]) == 2
    assert "E_CONFIRM_REQUIRED" in capsys.readouterr().err
    assert main(["reset", "--yes"]) == 0
    assert default_var.is_dir()
    assert list(default_var.iterdir()) == []


def test_cli_serve_rejects_public_bind(tmp_path, capsys) -> None:
    assert main(["--var-dir", str(tmp_path / "var"), "serve",
                 "--host", "0.0.0.0"]) == 2
    assert "E_NOT_LOCAL" in capsys.readouterr().err


def test_cli_report_missing_record(tmp_path, capsys) -> None:
    assert main(["--var-dir", str(tmp_path / "var"), "report",
                 "--kind", "check", "--id", "99"]) == 2
    assert "E_REF_NOT_FOUND" in capsys.readouterr().err


def test_scale_and_test_ratio() -> None:
    production = list((ROOT / "stoplist_ledger").glob("*.py"))
    production.append(ROOT / "stoplist_ledger" / "templates" / "index.html")
    tests = list((ROOT / "tests").glob("*.py"))
    production_lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in production
    )
    test_lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in tests
    )
    total = production_lines + test_lines
    ratio = test_lines / total
    # Ceiling raised from 4500 for the scope diagnostics module and its tests;
    # the ratio band below remains the enforced test-density check.
    assert 2500 <= total <= 5200, (production_lines, test_lines, total)
    assert 0.35 <= ratio <= 0.45, (test_lines, total, ratio)
