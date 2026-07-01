# Changelog

All notable changes to coldframe. Format loosely follows [Keep a Changelog]; versions are [SemVer].

## [Unreleased]

The engine, CLI, MCP server, and Claude Code plugin are built and tested. The core auto-memory loop
is **verified end-to-end against real Claude Code** (headless `claude -p`): a fact stated in one
session is captured and recalled (unprompted) in the next. Remaining before tagging `v0.1.0`:
confirm the `claude plugin install` path + register the PyPI trusted publisher, then
`git tag v0.1.0 && git push` (the Release workflow publishes to PyPI + attaches binaries).

## [0.1.0] — unreleased

First public version. Local-first, ownable memory for AI agents — one SQLite file, offline, no key.

### Added

- **Memory engine** — hybrid retrieval (BM25 + numpy-KNN vectors, RRF fusion), bi-temporal versions
  with `as_of` rewind (`search --as-of`), deterministic dedup/conflict resolution, decay +
  consolidation (+ an archive-imminent strength sub-label) + per-scope caps, and a token-budget
  packer. (The v1-scope trim cut the search-time graph edge channel — edge rows kept — plus the
  deterministic tagger and the opt-in LLM rerank surface; see ADR-D27.)
- **CLI** — `add` / `search` / `list` / `show` / `timeline` / `doctor` / `consolidate` / `worker` /
  `jobs` / `export` / `import` / `purge` / `reembed` / `ui` / `mcp` / `setup` / `hook`.
- **Claude Code integration** — a plugin (MCP server + recall/capture hooks + a capture skill) for
  one-install automatic memory: SessionStart + UserPromptSubmit recall, Stop-hook capture with a
  keyless naive backstop, and agent-push capture (the agent calls `add_memory` in-session — no key,
  no extra metered cost). Per-project + global scoping by git project.
- **Local web UI** (`cold-frame ui`) — a dashboard of memory strength/decay; view + edit
  (pin/correct/forget), CSRF-guarded, localhost-only.
- **Safety / privacy** — obvious secrets blocked before disk; grep-verified hard `purge`;
  content-free logs; **opt-in PII redaction** (email/phone/card/ssn — `add --redact-pii` /
  `Memory(pii_redact=…)`); **opt-in at-rest encryption** (the `[crypto]` extra = SQLCipher;
  `Memory(encryption_key=…)` / `$COLD_FRAME_KEY`; whole DB + WAL + snapshots).
- **Distribution** — `uv tool` / `pipx`, a Homebrew tap formula, and a standalone single-file binary
  (no Python needed); a Release workflow that publishes on tag.

### Known limitations (planned / deferred)

- PII redaction + at-rest encryption are OFF by default (opt-in); encryption is set at DB creation.
  There's no *in-place* migration, but `cold-frame encrypt --out enc.db` writes an encrypted copy of
  an existing plaintext DB (non-destructive; needs the `[crypto]` extra).
- The raw-chat-to-a-REMOTE-extractor exposure is now narrowed: an obvious secret (or an ambiguous
  high-entropy span) in the chat forces a fallback to LOCAL naive extraction, so it never reaches a
  remote endpoint. Residual: a non-pattern secret the deterministic scan misses could still be sent.
- Admission confidence-gate + opt-in `require_consent` (hold every new memory for approval) are
  BUILT (`Memory(confidence_gate=…, require_consent=…)`); `cold-frame rekey` rotates the at-rest key.
- The I7 local-LLM admission tiebreak was REMOVED (ADR-I7-cut): dead in prod (no local LLM ships) and
  it silently fail-closed-BLOCKed legit facts with a high-entropy token. Admission is now a
  deterministic secret-scan only; the ambiguous entropy band proceeds (real secrets still caught by
  the vendor-prefix patterns + ≥4.5 entropy). I7's "no secret span reaches a remote endpoint" still
  holds via the pre-disk BLOCK + the remote-extraction egress fallback.
- Event-log replay import is BUILT (`cold-frame import <log.ndjson> --events` / `Memory.import_events`
  — idempotent, last-writer-wins by HLC, note-only). Deferred to v1.1/hosted: *per-note* crypto-shred
  (per-record envelope keys — rekey is whole-DB rotation), and cross-device conflict resolution
  beyond last-writer-wins.
- The agent-push capture path is model-discretionary; the keyless backstop guarantees coverage.
- Write/triage in the browser is partial; full event-log replay import is snapshot-based for now.

[Keep a Changelog]: https://keepachangelog.com/
[SemVer]: https://semver.org/
