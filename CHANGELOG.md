# Changelog

All notable changes to coldframe. Format loosely follows [Keep a Changelog]; versions are [SemVer].

## [Unreleased]

The engine, CLI, MCP server, and Claude Code plugin are built and tested; a tagged `v0.1.0` PyPI
release is pending a once-through live verification in a real Claude Code session (see the repo's
readiness notes). Cut a release with `git tag v0.1.0 && git push origin v0.1.0` once verified — the
Release workflow publishes to PyPI + attaches per-platform binaries.

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

- PII redaction and at-rest encryption are not yet automatic (obvious secrets are blocked).
- The agent-push capture path is model-discretionary; the keyless backstop guarantees coverage.
- Write/triage in the browser is partial; full event-log replay import is snapshot-based for now.

[Keep a Changelog]: https://keepachangelog.com/
[SemVer]: https://semver.org/
