"""One-command end-to-end demonstration and the guarded reset helper."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .checker import check_declaration
from .clock import FixedClock
from .compiler import compile_declaration
from .errors import fail
from .impact import create_impact
from .registry import register_file
from .report import default_report_path, write_report
from .snapshot import snapshot_many
from .store import LedgerStore
from .util import PROJECT_ROOT, display_path, pretty_json

FIXTURES = PROJECT_ROOT / "fixtures"
REPORT_KINDS = ("json", "md")


def safe_reset(target: Path, var_root: Path, confirmed: bool = False) -> Path:
    """Remove the var tree (or one child), refusing anything outside var_root."""

    if not confirmed:
        fail("E_CONFIRM_REQUIRED", "reset requires explicit confirmation (--yes)")
    resolved_root = Path(var_root).resolve()
    resolved_target = Path(target).resolve()
    if resolved_target == resolved_root:
        if resolved_root.exists():
            for child in sorted(resolved_root.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        return resolved_root
    if resolved_root not in resolved_target.parents:
        fail(
            "E_UNSAFE_RESET",
            "reset target must be the var directory or a child of it",
            target=str(resolved_target),
            var_root=str(resolved_root),
        )
    if resolved_target.is_dir() and not resolved_target.is_symlink():
        shutil.rmtree(resolved_target)
    elif resolved_target.exists():
        resolved_target.unlink()
    return resolved_target


def register_fixtures(store: LedgerStore, clock: FixedClock,
                      operator: str) -> list[dict[str, Any]]:
    paths = sorted(
        [path for path in (FIXTURES / "stoplists").iterdir()
         if path.suffix.lower() in {".txt", ".yaml", ".yml"}],
        key=lambda path: path.name)
    registered = []
    for path in paths:
        registered.append(register_file(store, path, clock, operator=operator,
                                        allow_existing=True))
    return registered


def _report(payload: dict[str, Any], report_dir: Path, kind: str,
            identifier: str,
            formats: tuple[str, ...] = ("json", "md")) -> list[str]:
    artifacts = []
    for fmt in formats:
        path = default_report_path(report_dir, kind, identifier, fmt)
        write_report(payload, fmt, path)
        artifacts.append(display_path(path))
    return artifacts


def run_demo(var_dir: Path, clock: FixedClock,
             operator: str = "op-demo-01") -> dict[str, Any]:
    var_dir = Path(var_dir)
    store = LedgerStore(var_dir / "ledger.db").initialize()
    report_dir = var_dir / "reports"
    register_fixtures(store, clock, operator)

    comp_a = compile_declaration(
        store, FIXTURES / "declarations" / "pipeline_a.yaml", clock, operator
    )
    texts = sorted((FIXTURES / "texts").glob("*.txt"), key=lambda path: path.name)
    snaps_a = snapshot_many(store, comp_a["id"], texts, clock, operator)
    comp_b = compile_declaration(
        store, FIXTURES / "declarations" / "pipeline_b.yaml", clock, operator
    )
    snaps_b = snapshot_many(store, comp_b["id"], texts, clock, operator)
    impact = create_impact(
        store, [snapshot["id"] for snapshot in snaps_a],
        [snapshot["id"] for snapshot in snaps_b], clock, operator)
    check_a = check_declaration(
        store, FIXTURES / "declarations" / "pipeline_a.yaml", clock, operator
    )
    check_broken = check_declaration(
        store, FIXTURES / "declarations" / "broken_conflict.yaml", clock, operator
    )

    artifacts: list[str] = []
    artifacts += _report(comp_a, report_dir, "compilation", comp_a["name"])
    artifacts += _report(comp_b, report_dir, "compilation", comp_b["name"])
    for snapshot in snaps_a + snaps_b:
        name = f"{snapshot['compilation']['name']}_{snapshot['text_id']}"
        artifacts += _report(snapshot, report_dir, "snapshot", name,
                             ("json",))
    artifacts += _report(impact, report_dir, "impact",
                         f"{comp_a['name']}_to_{comp_b['name']}")
    artifacts += _report(check_a, report_dir, "check", comp_a["name"])
    artifacts += _report(check_broken, report_dir, "check",
                         "broken-conflict")
    summary = {
        "kind": "demo",
        "schema_version": 1,
        "generated_at": clock.timestamp(),
        "counts": store.summary(),
        "compilations": {
            comp_a["name"]: {"id": comp_a["id"],
                             "rules_hash": comp_a["rules_hash"],
                             "rule_count": comp_a["rule_count"]},
            comp_b["name"]: {"id": comp_b["id"],
                             "rules_hash": comp_b["rules_hash"],
                             "rule_count": comp_b["rule_count"]},
        },
        "snapshots": {
            comp_a["name"]: [_snap_meta(item) for item in snaps_a],
            comp_b["name"]: [_snap_meta(item) for item in snaps_b],
        },
        "impact": {"id": impact["id"], "report_hash": impact["report_hash"],
                   "summary": impact["summary"]},
        "checks": {
            comp_a["name"]: {"id": check_a["id"], "status": check_a["status"],
                             "total_issues": check_a["summary"]["total_issues"]},
            "broken-conflict": {
                "id": check_broken["id"], "status": check_broken["status"],
                "total_issues": check_broken["summary"]["total_issues"]},
        },
    }
    summary_paths = []
    for fmt in REPORT_KINDS:
        path = default_report_path(report_dir, "demo", "summary", fmt)
        write_report(summary, fmt, path)
        summary_paths.append(display_path(path))
    summary["artifacts"] = sorted(artifacts + summary_paths)
    for fmt in REPORT_KINDS:
        write_report(summary, fmt,
                     default_report_path(report_dir, "demo", "summary", fmt))
    return summary


def _snap_meta(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot["id"],
        "text_id": snapshot["text_id"],
        "result_hash": snapshot["result_hash"],
        "summary": snapshot["summary"],
    }


def demo_main(var_dir: Path, clock: FixedClock,
              operator: str = "op-demo-01") -> int:
    print(pretty_json(run_demo(var_dir, clock, operator)), end="")
    return 0
