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
  with `as_of` rewind, deterministic dedup/conflict resolution, decay + consolidation + per-scope
  caps (bounded active set), and a token-budget packer.
- **CLI** — `add` / `search` / `list` / `show` / `timeline` / `doctor` / `consolidate` / `worker` /
  `jobs` / `export` / `import` / `purge` / `reembed` / `ui` / `mcp` / `setup` / `hook`.
- **Claude Code integration** — a plugin (MCP server + recall/capture hooks + a capture skill) for
  one-install automatic memory: SessionStart + UserPromptSubmit recall, Stop-hook capture with a
  keyless naive backstop, and agent-push capture (the agent calls `add_memory` in-session — no key,
  no extra metered cost). Per-project + global scoping by git project.
- **Local web UI** (`cold-frame ui`) — a dashboard of memory strength/decay; view + edit
  (pin/correct/forget), CSRF-guarded, localhost-only.
- **Safety** — obvious secrets blocked before disk; grep-verified hard `purge`; content-free logs.
- **Distribution** — `uv tool` / `pipx`, a Homebrew tap formula, and a standalone single-file binary
  (no Python needed); a Release workflow that publishes on tag.

### Known limitations (planned)

- PII redaction (email/phone/card/ssn) is available opt-in (`add --redact-pii` / `Memory(pii_redact=…)`),
  off by default. At-rest encryption is available opt-in via the `[crypto]` extra (SQLCipher;
  `Memory(encryption_key=…)` / `$COLD_FRAME_KEY`) — whole DB + WAL + snapshots, set at creation;
  obvious secrets are always blocked pre-disk regardless.
- The agent-push capture path is model-discretionary; the keyless backstop guarantees coverage.
- Write/triage in the browser is partial; full event-log replay import is snapshot-based for now.

[Keep a Changelog]: https://keepachangelog.com/
[SemVer]: https://semver.org/
