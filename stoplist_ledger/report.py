"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .errors import fail
from .util import canonical_json, pretty_json, write_text

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def default_report_path(report_dir: Path, kind: str, identifier: str,
                        fmt: str) -> Path:
    suffix = "md" if fmt == "md" else "json"
    safe = _SAFE.sub("_", identifier).strip("._") or "report"
    return Path(report_dir) / f"{kind}_{safe}.{suffix}"


def write_report(payload: dict[str, Any], fmt: str, path: Path) -> Path:
    if fmt == "json":
        return write_text(Path(path), pretty_json(payload))
    if fmt == "md":
        return write_text(Path(path), render_markdown(payload))
    fail("E_BAD_FORMAT", "report format must be json or md", format=fmt)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return lines


def _source_label(source: dict[str, Any] | None) -> str:
    if not source:
        return "-"
    if source.get("kind") == "default":
        return "default:keep"
    return f"{source.get('table_id')}@v{source.get('version')} {source.get('rule')}"


def _metadata(payload: dict[str, Any]) -> list[str]:
    keys = ["kind", "name", "scope", "rules_hash", "declaration_hash",
            "result_hash", "report_hash", "text_id", "generated_at",
            "created_at", "status"]
    rows = [(key, payload[key]) for key in keys if payload.get(key) is not None]
    if not rows:
        return []
    return ["## Metadata", ""] + _table(["Field", "Value"], rows) + [""]


def _compilation(payload: dict[str, Any]) -> list[str]:
    lines = _metadata(payload)
    lines += ["## Tables and priority", ""]
    lines += _table(
        ["Table", "Version", "Kind", "Entries", "Content hash"],
        [(table["id"], table["version"], table["kind"], table["entry_count"],
          table["content_hash"][:16]) for table in payload.get("tables", [])])
    lines += ["", f"Priority (high to low): {', '.join(payload.get('priority', []))}", ""]
    lines += ["## Effective rules", ""]
    rows = []
    for rule in payload.get("rules", []):
        overrides = ", ".join(
            f"{item['source']['table_id']}@v{item['source']['version']}"
            f":{item['disposition']}"
            for item in rule.get("overridden_by", []))
        rows.append((rule["term"], rule["disposition"], rule["target"],
                     _source_label(rule["source"]), overrides or "-"))
    lines += _table(["Term", "Disposition", "Target", "Decision source",
                     "Overridden by"], rows)
    counts = payload.get("disposition_counts", {})
    lines += ["", "## Summary", ""]
    lines += [f"- Rules: {payload.get('rule_count', len(payload.get('rules', [])))}"]
    lines += [f"- {key}: {counts[key]}" for key in sorted(counts)]
    return lines


def _snapshot(payload: dict[str, Any]) -> list[str]:
    lines = _metadata(payload)
    summary = payload.get("summary", {})
    lines += ["## Summary", ""]
    lines += [f"- {key}: {summary[key]}" for key in sorted(summary)]
    lines += ["", "## Token trace", ""]
    rows = []
    for token in payload.get("tokens", []):
        overrides = len(token.get("overridden_by", []))
        rows.append((token["index"], token["raw"],
                     f"{token['start']}..{token['end']}", token["term"],
                     token["disposition"], token["target"],
                     _source_label(token.get("source")), overrides or "-"))
    lines += _table(["#", "Raw", "Offsets", "Normalized", "Disposition",
                     "Target", "Source", "Overrides"], rows)
    return lines


def _check(payload: dict[str, Any]) -> list[str]:
    lines = _metadata(payload)
    summary = payload.get("summary", {})
    lines += ["## Summary", ""]
    lines += [f"- Total issues: {summary.get('total_issues', 0)}"]
    by_code = summary.get("by_code", {})
    lines += [f"- {code}: {count}" for code, count in sorted(by_code.items())]
    lines += ["", "## Findings", ""]
    if not payload.get("findings"):
        lines.append("No consistency issues detected.")
        return lines
    lines += _table(
        ["Code", "Name", "Severity", "Subject", "Message"],
        [(finding["code"], finding["name"], finding["severity"],
          finding["subject"], finding["message"])
         for finding in payload["findings"]])
    lines += ["", "### Finding details", ""]
    for finding in payload["findings"]:
        lines += [f"#### {finding['code']} {finding['subject']}", "",
                  "```json", canonical_json(finding["details"]), "```", ""]
    return lines


def _outcome(value: dict[str, Any] | None) -> str:
    if value is None:
        return "missing"
    target = f" -> {value['target']}" if value.get("target") else ""
    return f"{value['disposition']}{target}"


def _impact(payload: dict[str, Any]) -> list[str]:
    lines = _metadata(payload)
    left = payload["left"]["compilation"]
    right = payload["right"]["compilation"]
    lines += ["## Compared compilations", ""]
    lines += _table(
        ["Side", "Compilation", "Scope", "Rules hash"],
        [("left", left["name"], left["scope"], left["rules_hash"][:16]),
         ("right", right["name"], right["scope"], right["rules_hash"][:16])],
    )
    summary = payload.get("summary", {})
    lines += ["", "## Summary", ""]
    lines += [f"- {key}: {summary[key]}" for key in sorted(summary)]
    change_sections = [
        ("new_dropped", "Newly dropped terms"),
        ("gone_dropped", "Terms no longer dropped"),
        ("merge_target_changes", "Merge target changes"),
        ("exemption_changes", "Whitelist exemption changes"),
    ]
    for key, title in change_sections:
        lines += ["", f"## {title}", ""]
        rows = [
            (item["term"], _outcome(item["before"]), _outcome(item["after"]))
            for item in payload["rule_changes"][key]
        ]
        lines += _table(["Term", "Before", "After"], rows) if rows else [
            "No changes in this category."
        ]
    lines += ["", "## Affected texts", ""]
    affected = [item for item in payload["text_changes"] if item["affected"]]
    if not affected:
        lines.append("No token outcomes changed.")
    for item in affected:
        lines += [f"### {item['text_id']} ({item['affected_tokens']} affected tokens)",
                  ""]
        lines += _table(
            ["#", "Raw", "Term", "Before", "After"],
            [(change["index"], change["raw"], change["term"],
              _outcome(change["before"]), _outcome(change["after"]))
             for change in item["changes"]])
        lines.append("")
    return lines


def _summary(payload: dict[str, Any]) -> list[str]:
    lines = _metadata(payload)
    for heading, key in (("Counts", "counts"), ("Artifacts", "artifacts"),
                         ("Notes", "notes")):
        value = payload.get(key)
        if value is None:
            continue
        lines += [f"## {heading}", ""]
        if isinstance(value, dict):
            lines += [f"- {name}: {value[name]}" for name in sorted(value)]
        elif isinstance(value, list):
            lines += [f"- {item}" for item in value]
        else:
            lines += [str(value)]
        lines.append("")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    kind = payload.get("kind", "report")
    title = f"Stoplist Ledger {kind.replace('_', ' ').title()} Report"
    renderers = {
        "compilation": _compilation,
        "snapshot": _snapshot,
        "check": _check,
        "impact": _impact,
        "demo": _summary,
    }
    body = renderers.get(kind, _summary)(payload)
    text = [f"# {title}", "", "Generated by the offline stoplist-ledger.", ""]
    text.extend(body)
    return "\n".join(text).rstrip() + "\n"
