"""Command-line entry point for stoplist-ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .checker import check_declaration
from .clock import FixedClock, make_clock
from .compiler import compile_declaration
from .demo import demo_main, safe_reset
from .errors import LedgerError, fail
from .impact import create_impact
from .registry import register_file
from .report import default_report_path, write_report
from .snapshot import snapshot_many, snapshot_text
from .store import LedgerStore
from .util import pretty_json

DEFAULT_VAR = Path("var")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stoplist_ledger",
        description="Offline version governance for stoplists and synonym tables.",
    )
    parser.add_argument("--var-dir", default=str(DEFAULT_VAR),
                        help="local ledger and artifact directory (default: var)")
    parser.add_argument("--operator", default="op-demo-01",
                        help="synthetic operator label")
    parser.add_argument("--now", default=None,
                        help="deterministic ISO-8601 clock value")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="initialize the local ledger")
    commands.add_parser("demo", help="run the complete deterministic demo")

    register = commands.add_parser("register", help="register table file versions")
    register.add_argument("paths", nargs="+", type=Path)
    register.add_argument("--kind", choices=["stoplist", "synonym", "noise",
                                             "whitelist"])
    register.add_argument("--table-id")
    register.add_argument("--version", type=int)

    compile_cmd = commands.add_parser("compile", help="compile a declaration")
    compile_cmd.add_argument("declaration", type=Path)
    _report_args(compile_cmd)

    snapshot_cmd = commands.add_parser("snapshot", help="snapshot one text")
    snapshot_cmd.add_argument("text", type=Path)
    snapshot_cmd.add_argument("--compilation", required=True)
    snapshot_cmd.add_argument("--scope")
    _report_args(snapshot_cmd)

    all_cmd = commands.add_parser("snapshot-all", help="snapshot a text directory")
    all_cmd.add_argument("--compilation", required=True)
    all_cmd.add_argument("--texts-dir", type=Path, default=Path("fixtures/texts"))
    all_cmd.add_argument("--format", choices=["json", "md"], default="json")
    all_cmd.add_argument("--out-dir", type=Path)

    diff_cmd = commands.add_parser("diff", help="compare two snapshot sets")
    diff_cmd.add_argument("--left", nargs="+", type=int)
    diff_cmd.add_argument("--right", nargs="+", type=int)
    diff_cmd.add_argument("--left-compilation", type=int)
    diff_cmd.add_argument("--right-compilation", type=int)
    _report_args(diff_cmd)

    check_cmd = commands.add_parser("check", help="run the consistency checker")
    check_cmd.add_argument("declaration", type=Path)
    _report_args(check_cmd)

    report_cmd = commands.add_parser("report", help="render a stored record")
    report_cmd.add_argument("--kind", required=True,
                            choices=["compilation", "snapshot", "check", "impact"])
    report_cmd.add_argument("--id", required=True, type=int)
    _report_args(report_cmd)

    serve = commands.add_parser("serve", help="start the read-only audit page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)

    reset = commands.add_parser("reset", help="clear the default var directory")
    reset.add_argument("--yes", action="store_true")
    reset.add_argument("--path", type=Path)
    return parser

def _report_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "md"], default="json")
    parser.add_argument("--out", type=Path)

def _context(args: argparse.Namespace) -> tuple[LedgerStore, FixedClock, Path]:
    clock = make_clock(args.now)
    var_dir = Path(args.var_dir)
    store = LedgerStore(var_dir / "ledger.db").initialize()
    return store, clock, var_dir

def _emit_report(record: dict[str, Any], args: argparse.Namespace,
                 identifier: str, var_dir: Path) -> Path:
    out = args.out or default_report_path(
        var_dir / "reports", record["kind"], identifier, args.format
    )
    write_report(record, args.format, out)
    return out

def _resolve_compilation(store: LedgerStore, value: str) -> dict[str, Any]:
    record = None
    if value.isdigit():
        record = store.get_compilation(int(value))
    if record is None:
        record = store.latest_compilation(value)
    if record is None:
        fail("E_REF_NOT_FOUND", "compilation does not exist", compilation=value)
    return record

def _snapshot_ids(store: LedgerStore, explicit: list[int] | None,
                  compilation_id: int | None) -> list[int]:
    if explicit:
        return sorted(explicit)
    if compilation_id is None:
        fail("E_BAD_FORMAT", "specify snapshot ids or a compilation id")
    records = store.list_snapshots(compilation_id)
    if not records:
        fail("E_REF_NOT_FOUND", "compilation has no snapshots",
             compilation_id=compilation_id)
    return [record["id"] for record in records]

def dispatch(args: argparse.Namespace) -> int:
    if args.command == "reset":
        target = args.path or DEFAULT_VAR.resolve()
        safe_reset(target, DEFAULT_VAR.resolve(), confirmed=args.yes)
        print(f"reset {Path(target).resolve()}")
        return 0
    if args.command == "demo":
        return demo_main(Path(args.var_dir), make_clock(args.now), args.operator)

    store, clock, var_dir = _context(args)
    if args.command == "init":
        print(pretty_json(store.summary()), end="")
        return 0
    if args.command == "register":
        if len(args.paths) > 1 and (args.table_id or args.version):
            fail("E_BAD_FORMAT", "--table-id/--version require a single file")
        saved = [
            register_file(store, path, clock, operator=args.operator,
                          kind=args.kind, table_id=args.table_id,
                          version=args.version)
            for path in args.paths
        ]
        print(pretty_json([
            {"id": item["id"], "table_id": item["table_id"],
             "version": item["version"], "kind": item["kind"],
             "content_hash": item["content_hash"]}
            for item in saved
        ]), end="")
        return 0
    if args.command == "compile":
        record = compile_declaration(store, args.declaration, clock, args.operator)
        path = _emit_report(record, args, record["name"], var_dir)
        print(pretty_json({"id": record["id"], "name": record["name"],
                           "rules_hash": record["rules_hash"],
                           "report": str(path)}), end="")
        return 0
    if args.command == "snapshot":
        compilation = _resolve_compilation(store, args.compilation)
        record = snapshot_text(store, compilation["id"], args.text, clock,
                               args.operator, args.scope)
        identifier = f"{compilation['name']}_{record['text_id']}"
        path = _emit_report(record, args, identifier, var_dir)
        print(pretty_json({"id": record["id"], "result_hash":
                           record["result_hash"], "report": str(path)}), end="")
        return 0
    if args.command == "snapshot-all":
        compilation = _resolve_compilation(store, args.compilation)
        texts = sorted(Path(args.texts_dir).glob("*.txt"))
        if not texts:
            fail("E_IO", "no .txt fixtures found", directory=str(args.texts_dir))
        records = snapshot_many(store, compilation["id"], texts, clock,
                                args.operator)
        outputs = []
        for record in records:
            out = args.out_dir or var_dir / "reports"
            path = default_report_path(
                out, "snapshot",
                f"{compilation['name']}_{record['text_id']}", args.format
            )
            write_report(record, args.format, path)
            outputs.append({"id": record["id"], "text_id": record["text_id"],
                            "result_hash": record["result_hash"],
                            "report": str(path)})
        print(pretty_json(outputs), end="")
        return 0
    if args.command == "check":
        record = check_declaration(store, args.declaration, clock, args.operator)
        path = _emit_report(record, args, args.declaration.stem, var_dir)
        print(pretty_json({"id": record["id"], "status": record["status"],
                           "total_issues": record["summary"]["total_issues"],
                           "report": str(path)}), end="")
        return 0
    if args.command == "diff":
        left = _snapshot_ids(store, args.left, args.left_compilation)
        right = _snapshot_ids(store, args.right, args.right_compilation)
        record = create_impact(store, left, right, clock, args.operator)
        identifier = (f"{record['left']['compilation']['name']}_to_"
                      f"{record['right']['compilation']['name']}")
        path = _emit_report(record, args, identifier, var_dir)
        print(pretty_json({"id": record["id"], "summary": record["summary"],
                           "report_hash": record["report_hash"],
                           "report": str(path)}), end="")
        return 0
    if args.command == "report":
        getter = {
            "compilation": store.get_compilation,
            "snapshot": store.get_snapshot,
            "check": store.get_check,
            "impact": store.get_impact,
        }[args.kind]
        record = getter(args.id)
        if record is None:
            fail("E_REF_NOT_FOUND", "record does not exist",
                 kind=args.kind, id=args.id)
        path = _emit_report(record, args, f"{args.kind}_{args.id}", var_dir)
        print(str(path))
        return 0
    if args.command == "serve":
        from .webapp import create_app, validate_bind_host

        validate_bind_host(args.host)
        app = create_app(store)
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
        return 0
    fail("E_BAD_FORMAT", "unknown command", command=args.command)

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except LedgerError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        if exc.details:
            print(pretty_json(exc.details), end="", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
