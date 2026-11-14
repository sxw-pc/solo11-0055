# Compliance and safety notes

## Fully offline by design

- Runtime code performs no HTTP, DNS, SMTP, socket, model-download or remote
  storage calls.
- Flask is used only to serve a loopback audit page. `serve` refuses any host
  other than `127.0.0.1`, `localhost` or `::1` with `E_NOT_LOCAL`.
- Templates, styles and scripts are local. The template contains no external
  font, image, script, stylesheet, analytics, CDN or preconnect reference.
- There are no corpus downloads and no NLP model weights.
- Dependencies are pinned in `requirements.txt`; installation is a deploy-time
  action, not a runtime network dependency.

The test suite monkeypatches `socket.connect`, `socket.create_connection` and
`socket.getaddrinfo` to fail and then runs the complete demo, proving the normal
pipeline does not open a socket. Additional tests inspect runtime sources for
external URL references.

## Read-only audit surface

The HTTP app exposes only GET routes:

- HTML views for tables, compilations, snapshots, checks and impacts;
- JSON GET endpoints for the same records and a summary.

There is no POST/PUT/PATCH/DELETE handler, no form, no login, no file upload and
no write API. `POST /api/tables` returns HTTP 405. The page is bound to the local
machine only by default.

## No secrets or real identifiers

- No API keys, tokens, passwords, certificates, private keys or connection
  strings exist in this repository.
- No real company names, person names, domains, IP targets, email addresses or
  physical addresses are used.
- Operator values are synthetic placeholders such as `op-demo-01`,
  `op-test-01` and `op-offline-01`.
- Dataset and text identifiers are synthetic (`dataset-alpha`, `sample_01`).
- Terms are generic placeholders. The small Chinese character set is limited to
  common function words used as stoplist examples, not copied from a corpus.
- Fixture texts are hand-authored synthetic sentences.

## Data handling expectations

This is an engineering demonstration, not a compliance certification,
anonymization tool or production data processor.

- Use it only with data you are authorized to process.
- Do not use it for mass targeting, surveillance, credential collection,
  detection evasion or any unlawful activity.
- Do not imply that whitelist/stoplist effects constitute de-identification.
  Lexical filtering does not remove personal information, and this project does
  not implement redaction.
- The project does not import real corpora or private user text. Any user-supplied
  local files remain local and are stored in the local SQLite ledger and reports.
- The ledger may contain operator labels and source paths. Review artifacts
  before sharing them outside their intended environment.
- Hash values are for version identity, not cryptographic protection of content.

## Destructive operation guardrails

`reset` requires explicit `--yes` and only accepts the default `var/` directory or
one of its descendants. Both the root and target are path-resolved first, so a
symlink or `..` segment cannot redirect deletion outside `var/`. Tests cover
confirmation refusal, out-of-tree refusal and safe root/child clearing.

`demo` is additive to the ledger and idempotent: repeated registrations,
compilations, snapshots and reports reuse identical records. It does not delete
user data.

## Deterministic and inspectable outputs

- Fixed clock by default; no nondeterministic wall-clock value is written.
- Sorted JSON keys, sorted rule/finding lists and explicit report ordering.
- No hidden remote calls, no telemetry, no background jobs.
- Every compilation token can be traced to a table version, source line and rule,
  with an override chain where priority resolved a conflict.
- Every check finding exposes the problematic terms, sources, cycles or version
  references needed for review.

## Out of scope

The project does not implement tokenizer quality research, language
identification, stemming, embeddings, Chinese word segmentation, a visual table
editor, multi-user permissions, remote synchronization, distributed compilation,
performance benchmarking, real-corpus import or privacy redaction. Requests for
those capabilities belong in separate, explicitly reviewed projects.
