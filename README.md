# stoplist-ledger

**A fully local version ledger, declarative compiler, effect-snapshot tool and
impact auditor for stoplists, synonym maps, noise blacklists and keep-word
whitelists.**

Small NLP and search teams often maintain these lexical tables in scattered
folders. A term is removed in one pipeline, a synonym target changes in another,
and nobody can reconstruct which table version governed a particular text.
`stoplist-ledger` treats lexical tables as versioned data assets: it registers
their content hashes and line diffs, compiles declarations into an auditable
rule tree, applies that tree to local text fixtures, and compares two effect
snapshots deterministically.

The project is a synthetic, offline engineering fixture. It contains no real
corpora, people, companies, domains or credentials.

## Quick start

Python 3.10+ is required. Install the pinned local dependencies, then use one of
the three primary commands.

```bash
python -m pip install -r requirements.txt

# One command: register -> compile A -> snapshot -> compile B -> snapshot ->
# impact -> checks -> reports, all under var/.
python -m stoplist_ledger demo

# Read-only local audit page at 127.0.0.1:5000.
python -m stoplist_ledger serve

# Full test suite; tests assert that no socket is opened.
pytest -q
```

`demo` prints a deterministic JSON summary and writes:

```text
var/ledger.db
var/reports/compilation_pipeline-a.{json,md}
var/reports/compilation_pipeline-b.{json,md}
var/reports/snapshot_pipeline-{a,b}_sample_0{1,2,3}.json
var/reports/impact_pipeline-a_to_pipeline-b.{json,md}
var/reports/check_pipeline-a.{json,md}
var/reports/check_broken-conflict.{json,md}
var/reports/demo_summary.{json,md}
```

Re-running `demo` reuses identical ledger records and rewrites byte-identical
reports. `python -m stoplist_ledger reset --yes` clears only the default `var/`
tree; any path outside it is refused.

## What it does

- **Register** `.txt` term lists and `.yaml` synonym maps under a table id and
  version. Each version stores a normalized content hash, counts, entries,
  line-level changes from the prior version and a synthetic operator label.
- **Compile** a YAML declaration describing table references, table scopes,
  normalization policy, scope label, merge-target allow-list and a high-to-low
  priority order. The output is a deterministic rule tree containing the
  winning disposition, source table/version/line/rule and every overridden
  proposal.
- **Snapshot** a local text fixture against one compilation. Tokens carry raw
  character offsets, normalized form, disposition (`keep`, `drop`, `merge`,
  `whitelist_exempt`), merge target, decision source and override trace.
- **Diff** two snapshot sets and their compilations. Reports new and removed
  dropped terms, merge-target changes, whitelist exemption changes, affected
  texts and affected token counts.
- **Check** eight consistency classes with stable codes and persist every report
  in the ledger.
- **Rebuild** the lineage of any registered `table@version`: entry-level
  changes are replayed from the table's first version, so each current entry
  carries its introducing version and its rename/retarget chain. Gaps in the
  version chain fail with `E_LINEAGE_GAP` instead of silently substituting
  another version.
- **Browse** the ledger through a local, read-only HTML page and JSON endpoints.

## CLI surface

```text
python -m stoplist_ledger [--var-dir var] [--operator op-demo-01] [--now ISO] \
  init
  register FILES... [--kind stoplist|synonym|noise|whitelist]
  compile DECLARATION [--format json|md] [--out PATH]
  snapshot TEXT --compilation ID_OR_NAME [--scope LABEL] [--format ...]
  snapshot-all --compilation ID_OR_NAME [--texts-dir DIR] [--format ...]
  diff --left-compilation ID --right-compilation ID [--format json|md]
  diff --left SNAPSHOT_IDS... --right SNAPSHOT_IDS... [--format json|md]
  check DECLARATION [--format json|md] [--out PATH]
  lineage TABLE[@vVERSION] [--version N] [--format json|md] [--out PATH]
  report --kind compilation|snapshot|check|impact --id ID [--format ...]
  serve [--host 127.0.0.1] [--port 5000]
  demo
  reset --yes [--path var/child]
```

Report generators default to `var/reports/`. `--format` selects JSON or
Markdown, and `--out` overrides the path. Timestamps come from the injected
fixed clock (`2024-01-01T00:00:00Z` unless `--now` is supplied).

## Fixtures

| Fixture | Purpose |
| --- | --- |
| `stoplists/zh_stop_v{1,2,3}.txt` | Synthetic Mandarin/Latin stoplists with deliberate version drift |
| `stoplists/synonym_v{1,2}.yaml` | Valid synonym maps; `gray` changes target between versions |
| `stoplists/noise_v1.txt` | Noise-word blacklist |
| `stoplists/whitelist_v{1,2}.txt` | Keep-word whitelists used to demonstrate exemptions |
| `stoplists/conflict_stop_v1.txt` | Negative-check fixture with a case-variant pair |
| `stoplists/broken_synonym_v1.yaml` | Negative-check fixture with a cycle and missing targets |
| `stoplists/empty_v1.txt` | Empty-table negative fixture |
| `declarations/pipeline_a.yaml` | Valid A pipeline (`zh_stop` v2, synonym v1, whitelist v1) |
| `declarations/pipeline_b.yaml` | Valid B pipeline (`zh_stop` v3, synonym v2, whitelist v2) |
| `declarations/broken_conflict.yaml` | Triggers all eight checker codes |
| `texts/sample_01..03.txt` | Small synthetic texts exercised by A and B |
| `expected/snapshot_01.json` | Golden token trace for A on sample 01 |
| `expected/impact_ab.json` | Golden A-to-B rule and text changes |

All tokens, terms and operator labels are placeholders. The Chinese characters
are generic function-word examples rather than corpus excerpts.

## Error codes

| Code | Meaning |
| --- | --- |
| `E_EMPTY_TERM` | Term or synonym target is empty after normalization |
| `E_CTRL_CHAR` | Term contains a control or format character |
| `E_TERM_TOO_LONG` | Normalized term exceeds 256 characters |
| `E_BAD_FORMAT` | File, declaration or argument shape violates its contract |
| `E_BAD_POLICY` | Unknown or malformed normalization policy |
| `E_DUP_CONTENT` | Identical normalized content is already registered |
| `E_DUP_VERSION` | Table id/version exists with different content |
| `E_DUP_PRIORITY` | Priority list repeats a table id |
| `E_REF_NOT_FOUND` | Referenced table version, compilation or snapshot is missing |
| `E_LINEAGE_GAP` | Version chain has gaps, so lineage cannot be rebuilt |
| `E_MERGE_TARGET_NOT_ALLOWED` | Merge target falls outside the declared allow-list |
| `E_DECL_INVALID` | Declaration is structurally invalid |
| `E_CONFIRM_REQUIRED` | `reset` was called without `--yes` |
| `E_UNSAFE_RESET` | Reset target is outside the default `var/` tree |
| `E_NOT_LOCAL` | Audit server was asked to bind a non-loopback host |
| `E_IO` | Local read or write failed |

Checker codes are separate: `C01` contradictory disposition, `C02` merge cycle,
`C03` missing merge target, `C04` case-variant duplicate, `C05` missing table
version, `C06` duplicate priority, `C07` empty table version, `C08` whitelist
term also covered by a drop table.

## Directory map

```text
stoplist_ledger/   CLI, ledger, registry, compiler, snapshot, impact, checker,
                   report renderer, read-only Flask UI, local template
fixtures/          synthetic tables, declarations, texts and golden results
tests/             unit, integration, determinism, offline and CLI tests
docs/              design, data contract and compliance notes
var/               generated ledger and reports (safe to clear with reset)
```

## Boundaries with sibling source projects

This repository does **not** crawl pages, redact or de-identify records, dedupe
bibliographic entries, calibrate metric definitions, archive time series,
govern a lakehouse, enforce measurement unit contracts or capture form
submissions. Its subject is narrowly the **lifecycle and effect semantics of
lexical table files**. Parsing is intentionally simple and deterministic; there
is no tokenizer model, language identification, embeddings or corpus ingestion.

## Tests and determinism

`pytest -q` covers registration and version diffs, normalization idempotence,
compile priority and allow-lists, snapshot hashes and offsets, A/B impact
ordering, all eight check codes, report rendering, the read-only web surface,
demo repetition, reset guards and socket denial. Golden files live under
`fixtures/expected/`.

All serialized artifacts use sorted-key canonical JSON, stable SHA-256 hashes,
fixed clocks and portable relative source paths. No timestamp, database row id
inside semantic hashes, or filesystem iteration order is allowed to influence a
hash.

See `docs/DESIGN.md`, `docs/DATA_CONTRACT.md` and `docs/COMPLIANCE.md`.
