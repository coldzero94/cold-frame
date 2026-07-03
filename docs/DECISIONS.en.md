# Key decisions (English summary)

The authoritative decision log ([`decisions.md`](decisions.md)) and risk analysis
([`risks.md`](risks.md)) are written in Korean (the project's planning language). This is a short
English summary of the load-bearing decisions for outside readers — the Korean docs win on detail.

## Architecture (see `CLAUDE.md` §3 for the full invariant list, I1–I17)

- **One SQLite file** holds facts + BM25 + vectors + edges + bi-temporal versions + provenance. No
  server, no account; works offline with no API key (default `HashEmbedder`, `llm=None`).
- **Sync core + one async seam** — every `Memory`/`Store`/`LLM` method is sync; the only `async` is
  the MCP stdio server, which wraps sync calls in a worker thread.
- **Deterministic engine first.** Freshness/archive/merge are decided by code (bi-temporal rules,
  decay, dedup bands); an LLM only *proposes* duplicate/contradiction verdicts, never decides.
- **Archive, not delete.** Forgetting/superseding archives rows (revivable); only secret/PII
  hard-purge deletes.

## D19 — Name

`cold-frame` (locked 2026-06). The gardening "cold frame" (a glass enclosure for tending plants) maps
to the memory-cultivation UX; also reads as cold-storage. (The original PyPI-name clearance became
moot when D28 dropped PyPI distribution.) Legal trademark filing is a separate external step that
does not block shipping.

## D25 — Security scope for v1

The always-on admission control is a **deterministic secret-BLOCK** (regex + entropy scan for API
keys / tokens / private keys, blocked before disk, zero LLM calls). PII redaction
(email/phone/card/ssn) and a consent/confidence gate are **built but OPT-IN** — a personal store
keeps your own contact facts by default. (At-rest encryption + `cold-frame rekey`, once built opt-in
here, were later **REMOVED** — see D29; *per-note* envelope crypto-shred was never built.) Rationale:
v1 is a local, single-user, user-owned file — low exposure surface. A grep-verified hard `purge`
exists for manual removal, and OS full-disk encryption covers at-rest.

## D26 — Automatic memory (the product)

Automatic recall + capture inside Claude Code is the product (not manual tool calls). Key facts:

- **MCP sampling is unsupported by Claude Code** (issue #1785), so coldframe cannot "pull" the host
  model. Capture is **agent-push**: a plugin skill instructs the agent to call `add_memory`
  in-session (uses the Claude you already pay for — no key, no extra metered cost), with a **keyless
  naive backstop** (the Stop hook) guaranteeing coverage; dedup merges the two.
- **Quality backends are opt-in** in `cold-frame worker`: the `claude` CLI (`claude -p`, session
  auth — but metered as programmatic usage), a local model (`[local-llm]`), or an API key.
- **Anti-bloat is the existing engine** — Layer-A salience filter, novelty/dedup, per-scope caps,
  consolidation. Auto-capture funnels through the same `WriteCore`, never a separate dump path.
- **Per-project + global** scoping by git project (remote URL → repo root), encoded on the scope.

## D27 — v1 scope trim (2026-07-01)

A multi-lens value audit re-scoped v1. The local-only LLM admission *tiebreak* was **removed**
(dead in prod — no local LLM ships — and it fail-closed-BLOCKed legitimate facts carrying
high-entropy tokens); admission is now the deterministic secret scan only, and the I7 invariant
(no secret span reaches a remote endpoint) holds via that scan plus the extraction egress guard.
Over-spec surfaces were cut: the search-time graph edge recall channel (edge *rows* stay), the
deterministic tagger (`derive_tags`), and the CLI/MCP rerank surface. Kept and shipped after
re-evaluation: `as_of` search rewind, the Vue web UI dashboard, and programmatic `rerank=True`.

## D28 — Distribution: Homebrew binary, not PyPI (2026-07-02)

Distribution is a **Homebrew tap + GitHub Release binaries** — a self-contained PyInstaller binary
per platform (CLI + MCP server + web UI in one file, no Python at runtime). PyPI publishing was
dropped (trusted-publisher registration blocked; a venv-style Homebrew formula can't fetch deps in
Homebrew's network-sandboxed build). Platforms: macOS Apple Silicon + Linux x86_64 (Intel Mac is
out of scope). The optional `[local-llm]` extra is not in the binary — install it from source
(`pip install 'cold-frame[local-llm] @ git+https://github.com/coldzero94/cold-frame'`).

## D29 — Remove at-rest encryption (2026-07-03)

Reverses D27's "keep crypto dormant". The at-rest encryption seam (the `[crypto]` SQLCipher extra,
the `encrypt`/`rekey` CLI commands, `Memory(encryption_key=…)` / `$COLD_FRAME_KEY`, and the keyed
connection/snapshot paths) is **removed entirely** — code and docs, −474 lines. For a local
single-user file it added ~0 value: OS full-disk encryption (FileVault/LUKS) already covers the
stolen-laptop threat, and the grep-verified plaintext `Store.purge` covers deliberate deletion. It
was pure maintenance + doc surface. v1's security value is unchanged — the deterministic pre-disk
secret-BLOCK plus grep-verified hard `purge`. If real at-rest encryption is ever needed it returns
in the hosted `[server]` layer, not local v1.
