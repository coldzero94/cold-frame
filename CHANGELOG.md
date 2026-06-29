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

- **Memory engine** — hybrid retrieval (BM25 + numpy-KNN vectors + a 1-hop graph edge channel,
  RRF fusion), bi-temporal versions with `as_of` rewind (`search --as-of`), deterministic
  dedup/conflict resolution, decay + consolidation (+ an archive-imminent strength sub-label) +
  per-scope caps (bounded active set), and a token-budget packer.
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

- PII redaction + at-rest encryption are OFF by default (opt-in); encryption is set at DB creation —
  there's no in-place plaintext→encrypted migration for an existing DB yet.
- Deferred to v1.1/hosted: the local admission LLM tiebreak (I7) + confidence-gate/consent, and a
  crypto-shred (key-destroy) purge. A cross-encoder/LLM rerank backend + auto-tagging are not wired.
- The agent-push capture path is model-discretionary; the keyless backstop guarantees coverage.
- Write/triage in the browser is partial; full event-log replay import is snapshot-based for now.

[Keep a Changelog]: https://keepachangelog.com/
[SemVer]: https://semver.org/
