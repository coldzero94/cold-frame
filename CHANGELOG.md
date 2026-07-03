# Changelog

All notable changes to coldframe. Format loosely follows [Keep a Changelog]; versions are [SemVer].

## [Unreleased]

### Removed

- **At-rest encryption (SQLCipher) is removed** (ADR-D29, reverses D27's "keep dormant"): the
  `[crypto]` extra, the `encrypt`/`rekey` CLI commands, `Memory(encryption_key=…)` / `$COLD_FRAME_KEY`,
  and the keyed connection/snapshot paths — −474 lines. For a local single-user file it added ~0
  value (OS full-disk encryption already covers stolen-laptop; the plaintext `purge` covers
  deliberate deletion) and was pure maintenance + doc surface. v1's at-rest story is now: keep the
  one `~/.cold-frame/memory.db` on an OS-encrypted disk. Unchanged: the deterministic pre-disk
  secret-BLOCK + grep-verified hard `purge` (the actual v1 security value).

## [0.1.1] — 2026-07-03

### Fixed

- **Release binaries now ship the web UI.** The release pipeline never built the Vue bundle (the
  git-ignored `_dist` was empty in CI) and PyInstaller dropped package data, so the v0.1.0 binaries
  silently served the fallback inline inspector. The build now fails loudly without the bundle and
  live-serves the frozen binary's UI as a smoke test.
- **Corrected the stale v0.1.0 Linux `sha256`** in the Homebrew formula (the linux binary was rebuilt
  during the Intel spec-out and re-attached with a new hash) — `brew install` on Linux x86_64 failed
  sha verification against the old pin. Surfaced while validating the new auto-bump.
- Runtime error messages and docs no longer point at PyPI installs that fail (ADR-D28: not on
  PyPI) — they give the working from-source command for the `[crypto]`/`[local-llm]`/`[mcp]` extras.
- The auto-generated Homebrew formula's `brew test` used a non-string matcher (raises on modern
  Ruby); releases also fail fast if the tag doesn't match the package version.

### Changed

- Releases auto-update the Homebrew tap formula (the `bump-tap` job): the tag is now the only manual
  release step — the workflow computes each binary's `sha256` and pushes the regenerated formula.
- Intel Mac (`macos-x86_64`) is out of scope: macOS = Apple Silicon only (+ Linux x86_64).

### Docs

- A README hero (the memory-field visualization — embers = memories, warmth = belief, blue = fading,
  hex glass = pinned) and a terminal demo (static SVG + a `vhs` tape for an animated GIF), both
  generated from committed data/scripts under `assets/`.

## [0.1.0] — 2026-07-02

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
- **Recall model (opt-in)** — the default is the offline lexical `HashEmbedder` (I5: no deps, no
  download); `COLD_FRAME_EMBEDDER=local` (needs the `[local-llm]` extra) switches CLI + MCP to a
  local `bge-small` semantic embedder. `Memory(embedder=…)` for programmatic use. Reembed after a
  switch (`cold-frame reembed`; `doctor` reports the stale-vector count).
- **Conflict detection (opt-in)** — supersession is always deterministic (`valid_at`, never the LLM),
  but *detecting* a contradiction is LLM-gated. `COLD_FRAME_LLM=claude` turns on the dedup/conflict
  judges for CLI `add` + MCP `add_memory` via the session-auth Claude CLI (the `worker` already
  auto-uses it); unset = the offline default (duplicate-merge + explicit corrections only).
- **Distribution** — a self-contained single-file binary (CLI + MCP, no Python needed) via a
  Homebrew tap (`brew install coldzero94/coldframe/cold-frame`) + GitHub Releases. The Release
  workflow builds + attaches the per-platform binaries on tag. NOT published to PyPI (the optional
  `[crypto]`/`[local-llm]` extras need a from-source install).

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
