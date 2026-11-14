# Data contract

This document defines every local file shape consumed or produced by
`stoplist-ledger`. All text is UTF-8. All JSON hashes use canonical sorted-key
JSON. The word "line" means a file line for `.txt` assets and a logical mapping
entry for YAML assets.

## 1. Table identity

Recommended naming: `<table_id>_v<version>.<txt|yaml>`. `table_id` uses ASCII
letters, digits, `_` or `-`, starts alphanumeric; `version` is a positive
integer. A version is immutable once registered. Registration can override
identity with `--table-id`/`--version`; when omitted or unparseable, the next
version for that table is used.

## 2. Term lists (`.txt`)

One term per line.

Rules:

- Blank lines are ignored.
- Lines whose trimmed form starts with `#` are comments and are counted in
  `comment_count`.
- Inline `#` does not begin a comment; the whole trimmed line is a term.
- Outer whitespace is removed before normalization.
- Duplicate terms are retained in file order. The compiler uses the first
  proposal from a table; duplicate identical proposals become supporting sources.
- Empty files are valid table versions and later trigger checker `C07` when
  referenced.

Per-term limits after normalization:

- non-empty (`E_EMPTY_TERM`);
- no Unicode `Cc` or `Cf` characters (`E_CTRL_CHAR`);
- at most 256 characters (`E_TERM_TOO_LONG`).

### 2.1 Wildcard pattern entries

A `.txt` entry whose normalized term carries `*` is a pattern instead of an
exact term. The wildcard is accepted only in these three shapes:

| Shape | Meaning | `pattern_kind` | `needle` |
| --- | --- | --- | --- |
| `pre*` | prefix match | `prefix` | `pre` |
| `*suf` | suffix match | `suffix` | `suf` |
| `*mid*` | substring match | `substring` | `mid` |

A bare `*`, more than two wildcards, or a wildcard anywhere other than the
leading/trailing edge fails registration with `E_BAD_PATTERN`. A star inside a
synonym YAML key is a literal exact term, never a pattern. Pattern entries keep
the same `line`/`raw` provenance as exact entries and additionally carry
`pattern_kind` and `needle`. The `content_hash` of any table containing a
pattern is computed from order-independent canonical entries, so reordering
exact and pattern lines cannot change it; pure exact tables keep their
historical file-order-sensitive hash.

Example:

```text
# synthetic stoplist
的
alpha
pre*
*suf
```

Table kinds: `stoplist`, `noise`, `whitelist`; these all use term-list files.
Kind is inferred from `synonym`, `noise`, `whitelist` or `stop` in the filename
(or defaults to `stoplist`) and can be set with `--kind`.

## 3. Synonym maps (`.yaml`)

Top-level mapping from source term to normalized target term; both must be
strings.

```yaml
# synthetic mapping
colour: color
gray: grey
```

- An empty mapping (`{}` or comment-only) is valid.
- Keys/targets are normalized under the registration policy.
- Targets are normalized again by the declaration policy at compile time.
- `synonym` kind is accepted only for `.yaml`/`.yml`; YAML is reserved for
  synonym maps.

## 4. Normalization

Case values: `case_sensitive` (no case/Unicode normalization), `lower` (Unicode
lower-case), `nfkc_lower` (NFKC then Unicode lower-case). Whitespace values:
`trim` (strip ends) and `collapse` (collapse internal runs to one ASCII space,
then strip). Normalization is asserted idempotent. The policy belongs to a
table's registration metadata and a declaration's compilation identity.

## 5. Declaration YAML

```yaml
name: pipeline-a                 # optional; defaults to file stem
scope: lang-mix                  # "*" means all scopes
normalization:
  case: nfkc_lower               # default
  whitespace: collapse           # default
tables:
  - id: zh_stop
    version: 2
    scopes: [lang-zh, lang-mix]  # default ["*"]
  - id: synonym
    version: 1
    scopes: ["*"]
  - id: noise
    version: 1
    scopes: [lang-mix]
  - id: whitelist
    version: 1
    scopes: ["*"]
priority: [whitelist, synonym, noise, zh_stop]   # high -> low
merge_targets: [color, grey]     # optional allow-list
```

Validation rules:

- Root must be a mapping; `tables` must be a non-empty list of unique ids.
- `version` is a positive integer; `scopes` is a non-empty string list.
- Every active table must appear once in `priority`. All priority ids must be
  declared. Duplicates fail compilation with `E_DUP_PRIORITY`.
- A table is active if the declaration scope is `*`/`all`, or the table scope
  contains `*`, `all` or the declaration scope.
- Missing normalizations default to `nfkc_lower` + `collapse`.
- If `merge_targets` is present, every effective merge target must appear in it
  after declaration-policy normalization.

Compiler failure codes include `E_DECL_INVALID`, `E_BAD_POLICY`,
`E_REF_NOT_FOUND`, `E_DUP_PRIORITY` and `E_MERGE_TARGET_NOT_ALLOWED`.

## 6. Stored table-version record

CLI/API JSON adds database metadata to the semantic record:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | integer | Ledger row id |
| `table_id`, `version`, `kind` | string/integer/string | Identity |
| `source_path` | string | Portable relative path |
| `policy` | object | `case` and `whitespace` |
| `content_hash` | 64-char hex | Normalized content hash |
| `line_count`, `entry_count`, `term_count`, `comment_count` | integer | Counts |
| `entries` | array | `line`, `raw`, `term`, optional `target_raw`/`target` |
| `diff` | object | See below |
| `operator`, `created_at` | string | Synthetic audit fields |

Diff object:

```json
{
  "base_version": 1,
  "added": [8],
  "removed": [],
  "modified": [{"old": 2, "new": 2}],
  "added_count": 1,
  "removed_count": 0,
  "modified_count": 1
}
```

## 7. Compilation JSON

```json
{
  "kind": "compilation",
  "schema_version": 1,
  "name": "pipeline-a",
  "declaration_path": "fixtures/declarations/pipeline_a.yaml",
  "declaration_hash": "...",
  "scope": "lang-mix",
  "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
  "priority": ["whitelist", "synonym", "noise", "zh_stop"],
  "tables": [
    {"id": "zh_stop", "version": 2, "kind": "stoplist",
     "scopes": ["lang-zh", "lang-mix"], "content_hash": "...",
     "entry_count": 7}
  ],
  "merge_targets": ["color", "grey"],
  "rules": [
    {"term": "alpha", "disposition": "drop", "target": null,
     "source": {"table_id": "zh_stop", "version": 2, "kind": "stoplist",
                "line": 7, "raw": "alpha", "rule": "stoplist:drop"},
     "overridden_by": []}
  ],
  "rule_count": 14,
  "disposition_counts": {"drop": 8, "merge": 2, "whitelist_exempt": 4},
  "rules_hash": "..."
}
```

Rules are sorted by `term`. `overridden_by` preserves priority order; each entry
contains the losing `disposition`, `target`, `source` and a reason. Equal losers
appear under `supporting_sources`.

Pattern rules carry `pattern_kind` (`prefix`/`suffix`/`substring`) and the
wildcard-free `needle`. When at least one pattern rule exists, the compilation
also carries a deterministic `pattern_index` with three searchable buckets
(`prefix`, `suffix`, `substring`), each sorted by rule `term`. Pure exact
compilations omit `pattern_index`, so their `rules_hash` is unchanged.

## 8. Snapshot JSON

```json
{
  "kind": "snapshot", "schema_version": 1,
  "compilation_id": 1, "text_id": "sample_01",
  "source_path": "fixtures/texts/sample_01.txt", "text_hash": "...",
  "scope": "lang-mix",
  "compilation": {"name": "pipeline-a", "rules_hash": "...", "scope": "lang-mix",
                  "rule_count": 14},
  "normalization": {"case": "nfkc_lower", "whitespace": "collapse"},
  "tokens": [
    {"index": 0, "raw": "Alpha", "start": 0, "end": 5, "term": "alpha",
     "disposition": "drop", "target": null,
     "source": {"table_id": "zh_stop", "version": 2, "kind": "stoplist",
                "line": 7, "raw": "alpha", "rule": "stoplist:drop"},
     "overridden_by": []}
  ],
  "summary": {"token_count": 7, "keep": 1, "drop": 3, "merge": 2,
              "whitelist_exempt": 1, "affected": 6},
  "result_hash": "..."
}
```

`start`/`end` are Python code-point indexes into the original text. Punctuation
and whitespace are separators, not tokens. `_` is retained inside words.
`keep` tokens use source kind `default` and rule `default:keep`.

When the compilation contains pattern rules, every token trace carries a
`match_type` of `exact`, `prefix`, `suffix` or `substring`, naming the rule that
won. When several rules match one token the winner is chosen by the specificity
sequence exact > prefix > suffix > substring; ties fall back to the declared
table priority and then to ascending pattern `term`, so the result never depends
on insertion order. Losing candidates are appended to `overridden_by` (after any
compile-time overrides) with a per-rule reason. Pure exact snapshots omit
`match_type` entirely.

`result_hash` covers text id/hash, compilation identity, scope, policy, tokens
and summary. It does not cover database ids, relative source path or timestamps.

## 9. Impact JSON

```json
{
  "kind": "impact",
  "schema_version": 1,
  "left": {"compilation": {"name": "pipeline-a", "rules_hash": "...",
                           "scope": "lang-mix"},
           "snapshots": [{"id": 1, "text_id": "sample_01", "result_hash": "..."}]},
  "right": {"compilation": {"name": "pipeline-b", "rules_hash": "...",
                            "scope": "lang-mix"},
            "snapshots": [{"id": 4, "text_id": "sample_01", "result_hash": "..."}]},
  "rule_changes": {
    "new_dropped": [{"term": "gamma", "before": {"disposition": "keep", "target": null},
                     "after": {"disposition": "drop", "target": null}}],
    "gone_dropped": [],
    "merge_target_changes": [],
    "exemption_changes": []
  },
  "text_changes": [
    {"text_id": "sample_01", "affected": true, "affected_tokens": 3,
     "before_hash": "...", "after_hash": "...",
     "changes": [{"index": 0, "raw": "Alpha", "term": "alpha",
                  "before": {"disposition": "drop", "target": null},
                  "after": {"disposition": "whitelist_exempt", "target": null}}]}
  ],
  "summary": {"new_dropped_terms": 1, "gone_dropped_terms": 0,
              "merge_target_changes": 0, "exemption_changes": 0,
              "affected_tokens": 3, "affected_texts": 1},
  "generated_at": "2024-01-01T00:00:00Z",
  "report_hash": "...",
  "id": 1,
  "created_at": "2024-01-01T00:00:00Z"
}
```

`text_changes` includes every paired text, even when `affected` is false.
`report_hash` covers the report body including the fixed `generated_at`, but not
the field itself or database metadata.

## 10. Check report and problem codes

```json
{
  "kind": "check", "schema_version": 1,
  "declaration_path": "fixtures/declarations/broken_conflict.yaml",
  "declaration_hash": "...", "scope": "lang-mix", "status": "issues",
  "findings": [
    {"code": "C01", "name": "contradictory_disposition", "severity": "warning",
     "subject": "alpha", "message": "the same term has multiple dispositions",
     "details": {"term": "alpha", "proposals": []}}
  ],
  "summary": {"total_issues": 9, "by_code": {"C01": 1}},
  "generated_at": "2024-01-01T00:00:00Z",
  "report_hash": "..."
}
```

| Code | Meaning | Severity |
| --- | --- | --- |
| `C01` | Same term disposed differently by non-whitelist tables | warning |
| `C02` | Directed synonym cycle | error |
| `C03` | Merge target missing from vocabulary/allow-list | error |
| `C04` | Case variants remain distinct under declaration policy | warning |
| `C05` | Referenced table version missing | error |
| `C06` | Duplicate priority id | error |
| `C07` | Referenced table version is empty | error |
| `C08` | Whitelist term also present in stop/noise table | warning |

Findings are sorted by code, subject, then canonical details. Reports deduplicate
by `report_hash`.

## 11. Error object

CLI failures print:

```text
E_CODE: human-readable message
{optional canonical details JSON}
```

The stable error-code list is in `README.md`. API failures use HTTP status codes;
there are no write APIs.

## 12. Compatibility

`schema_version` is `1` for compilation, snapshot, check and impact payloads. Any
future incompatible shape change must increment the relevant version rather than
silently reinterpreting existing hashes.
