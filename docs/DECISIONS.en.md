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
to the memory-cultivation UX; also reads as cold-storage. Name is free on PyPI + GitHub; legal
trademark filing is a separate external step that does not block shipping.

## D25 — Security scope for v1

v1 ships a **lightweight, deterministic secret-BLOCK only** (regex + entropy scan for API keys /
tokens / private keys, blocked before disk). **Deferred** to v1.1 / a hosted layer: automatic PII
redaction, consent gating, at-rest encryption, and crypto-shred purge. Rationale: v1 is a local,
single-user, user-owned file — low exposure surface, and "you manage your own secrets" is reasonable.
The README states this honestly; a grep-verified hard `purge` exists for manual removal.

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
