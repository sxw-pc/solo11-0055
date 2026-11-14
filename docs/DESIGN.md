# Design notes

## Components and data flow

```text
local table files ──register──> SQLite ledger (versions, hashes, diffs)
                                        │
declaration YAML ──compile─────────────> effective rule tree (JSON)
                                        │
local text fixture ──snapshot──────────> token trace + result hash
                                        │
two snapshot sets ──impact─────────────> rule/text change report
                                        │
declaration + ledger ──check──────────> C01..C09 report
                                        │
                      read-only Flask UI / JSON API / Markdown+JSON files
```

Every stage is a pure, local operation on files or SQLite. No component resolves
a hostname or opens an outbound socket.

## Ledger model

The database is `var/ledger.db` by default. It is created idempotently.

### `meta`

Key/value schema marker. Current schema version is `1`.

### `table_versions`

One row per registered table/version pair.

| Column | Meaning |
| --- | --- |
| `table_id`, `version` | Unique logical slot |
| `kind` | `stoplist`, `synonym`, `noise` or `whitelist` |
| `source_path` | Portable source filename/path |
| `policy_case`, `policy_whitespace` | Registration-time normalization policy |
| `content_hash` | SHA-256 of normalized content and kind; globally unique |
| `line_count`, `entry_count`, `term_count`, `comment_count` | Structural counts |
| `entries_json` | Ordered raw/normalized entries and line numbers |
| `diff_json` | Added, removed and modified line numbers relative to prior version |
| `operator`, `created_at` | Synthetic audit metadata |

An identical content hash is rejected before insertion. The same table/version
with different content is also rejected. Empty tables are permitted and later
flagged by `C07` when referenced.

### `compilations`

Stores declaration path/hash, scope, resulting `rules_hash`, complete rule-tree
JSON, operator and clock value. Duplicate `(name, rules_hash)` pairs reuse the
existing row, making repeated demos stable.

### `snapshots`

Stores compilation id, text id, portable source path, text hash, scope, result
hash, complete token trace and audit metadata. Duplicate
`(compilation_id, text_id, result_hash)` triples reuse the existing row.

### `checks` and `impacts`

Each report stores its canonical report hash and complete JSON payload. Report
hashes are unique, so repeating the same deterministic analysis does not append
duplicate rows.

### `audit`

Append-only synthetic event stream: `table.register`, `compilation.create`,
`snapshot.create`, `check.run`, `impact.create`, each with a subject and compact
detail payload.

## Content hashing and line diffs

Text tables are normalized under their registration policy. Synonym maps are
canonicalized as a sorted list of normalized `[source, target]` pairs. The kind
is part of the hash so identical terms interpreted as a stoplist and a whitelist
are not mistakenly treated as the same asset.

Line diffs use `difflib.SequenceMatcher` over raw source lines (or
`source => target` logical lines for YAML). Replace blocks produce modified line
pairs; unmatched leftovers become additions/removals. YAML entries use logical
mapping order because PyYAML's safe loader does not retain comments or exact
source offsets.

## Compilation algorithm

1. Parse and type-check the declaration. Unknown normalization keys and malformed
   references fail with `E_DECL_INVALID` or `E_BAD_POLICY`.
2. Determine which referenced tables are active for the declaration scope:
   a table applies when its scope list contains `*`, `all` or the declaration
   scope.
3. Load each active table version. Missing versions fail with
   `E_REF_NOT_FOUND`.
4. Validate uniqueness/completeness of the priority list.
5. Iterate tables in high-to-low priority and entries in file order. Each entry
   proposes:
   - `drop` for stoplist/noise;
   - `merge` plus normalized target for synonym;
   - `whitelist_exempt` for whitelist.
6. The first proposal for a normalized term wins. Later proposals with a
   different disposition/target enter the winner's `overridden_by` chain; equal
   proposals become `supporting_sources`.
7. Every winning merge proposal is then closed transitively: the chain walks
   winning proposals only, regardless of which table supplied each hop. The
   rule's `target` becomes the final endpoint and `merge_chain` records every
   hop (`term`, `target`, `source`) for audit. A chain that loops fails with
   `E_MERGE_CYCLE` (details carry the loop); a chain ending at a term dropped
   by a higher-priority table fails with `E_MERGE_TARGET_DROPPED`; a chain
   ending at a term absent from all tables fails with
   `E_MERGE_TARGET_MISSING` unless the allow-list vouches for it.
8. If `merge_targets` is present, the resolved final endpoint must normalize to
   an allow-list member, otherwise `E_MERGE_TARGET_NOT_ALLOWED`.
9. Sort rules by normalized term. Count dispositions. Hash scope, normalization,
   active tables (id/version/kind/content hash), priority and rules.

Each entry's `source` names the table id, version, kind, source line, raw form
and rule label such as `stoplist:drop`. This is enough to reconstruct why a term
won.

Scopes are labels, not language detection. A caller supplies a scope or uses the
compiled declaration scope; snapshotting with a mismatched scope is rejected.

## Snapshot algorithm

Tokenization is deliberately simple:

- scan Unicode code points;
- break on whitespace;
- break on Unicode punctuation (category `P*`) except `_`, which is kept as part
  of word-like identifiers;
- retain raw start/end offsets (Python code-point indexes);
- symbols such as `+` are not punctuation and remain inside a token.

Each token is normalized with the compilation policy and looked up in the rule
tree. If absent, it gets `keep` and a synthetic `default:keep` source. A merge
token records its target; a whitelist token records `whitelist_exempt`.
Overridden proposal metadata is copied into the token trace.

The semantic result contains text id/hash, compilation name/rules hash/scope,
normalization policy, token rows and disposition counts. `result_hash` is
SHA-256 over that semantic object. Database ids and timestamps are outside the
hash.

## Impact algorithm

Two snapshot sides are validated, sorted and grouped by compilation. A side may
contain multiple texts but must reference exactly one compilation.

**Rule level.** Union all normalized terms from both rule trees. Compare outcome
tuples `(disposition, target)`, treating absent terms as `keep`:

- `new_dropped`: right is drop, left is not;
- `gone_dropped`: left is drop, right is not;
- `merge_target_changes`: either side is merge and targets differ;
- `exemption_changes`: whitelist-exempt status changes in either direction.

**Text level.** Pair snapshots by text id and compare token outcomes by index. If
text hashes differ, every right token is recorded as a change. Each text entry
contains the affected count and before/after token outcomes.

Terms are sorted lexicographically, texts by id and token changes by index.
Summary counts are derived from those sorted structures. `report_hash` hashes the
full report payload, including the fixed generated timestamp, but excluding the
hash itself.

## Consistency checks

The checker intentionally remains useful when a declaration would not compile:

- `C01` contradictory non-whitelist dispositions (drop vs merge);
- `C02` directed synonym cycles, canonicalized to a stable rotation;
- `C03` merge chains whose final endpoint is absent from all referenced table
  terms and the allow-list;
- `C04` case variants that remain distinct under the declaration's normalization;
- `C05` missing table version;
- `C06` duplicate priority entry;
- `C07` referenced empty table version;
- `C08` whitelist/drop overlap;
- `C09` merge chains whose final endpoint is dropped by a higher-priority table.

`C02`, `C03` and `C09` walk the same priority-resolved winning proposals as the
compiler (via `closure.py`), so a chain the checker flags always fails
compilation with the matching error and vice versa.

Findings are sorted by code, subject and canonical details. The report stores code
counts and a stable hash.

## Determinism guarantees

- `clock.py` supplies a fixed default clock; report timestamps are injected.
- All JSON hashing uses sorted keys, UTF-8, no insignificant whitespace and
  rejects NaN.
- Rules, findings, snapshots and impact lists are explicitly sorted.
- Input files are traversed in filename order.
- Source paths are made relative to the project or working directory and never
  enter semantic hashes.
- SQLite row ids appear only as audit/user-facing identifiers, not inside
  semantic rule/snapshot hashes.
- Repeated compilations and snapshots reuse identical rows.
- Reset resolves paths before checking that the target equals or descends from
  the default `var/` directory.

## Known simplifications

No stemming, language detection, phrase matching, word segmentation model,
distributed compilation, table editing or remote synchronization is
implemented. Synonym cycles are reported, not repaired. Scopes are opaque
placeholder labels.
