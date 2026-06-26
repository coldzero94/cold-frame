# cold-frame

**A memory that belongs to you — and to your AI agent.** One local SQLite file remembers
facts about you (with full-text + vector search, version history, and the ability to rewind
what was true *when*). It runs offline, needs no API key, and plugs straight into **Claude Code**.

- 🗃️ **One file you own** — everything lives in `~/.cold-frame/memory.db`. Back it up by copying it; delete it to forget everything. No server, no account.
- 🔌 **Offline by default** — `add` → `search` works with zero keys and zero network.
- 🤖 **Claude-native** — ships an MCP server, so Claude Code can read and write your memory as it works.
- ⏪ **Rewindable belief** — facts are corrected and superseded, never silently overwritten; you can ask "what did I believe back in March?"

---

## Install

From a source checkout (a PyPI release is pending name clearance — see Status):

```bash
git clone <this repo> && cd cold-frame
uv sync --extra mcp        # core + the Claude Code / MCP server
# then prefix the commands below with `uv run`, e.g. `uv run cold-frame search "coffee"`
```

The core depends only on `pydantic` + `numpy`. Everything heavier (the MCP SDK, cloud LLMs,
the web UI) is an opt-in extra, so a plain install stays tiny.

---

## 30-second quickstart

```bash
cold-frame add "I prefer dark roast coffee."
cold-frame add "I switched jobs to Anthropic in 2026."
cold-frame search "what coffee do I like?"
cold-frame doctor          # health check: counts, integrity, embedder
```

No setup, no key. Your facts are saved the moment you add them.

---

## Use it from Claude Code

This is the point. Two commands give Claude Code persistent, **automatic** memory — it recalls what
matters at the start of each session and captures the durable facts as you work, no "remember this"
needed:

```bash
cold-frame hook install                       # recall + capture hooks (~/.claude)
claude mcp add cold-frame -- cold-frame mcp    # the capture drain + memory tools
```

That's the whole setup. See [Automatic memory](#automatic-memory-opt-in) below for how it stays
lean instead of hoarding everything.

You also get six explicit memory tools, for when you want to drive memory by hand:

| tool | what it does |
|------|--------------|
| `search_memory` | recall relevant facts for a query |
| `add_memory` | remember a fact (de-duplicated against what you already know) |
| `create_fact` | assert a single fact directly |
| `update_fact` | correct a fact (the old version is archived, still revivable) |
| `supersede` | replace a fact with a newer truth |
| `forget` | archive a fact (revivable — nothing is truly deleted) |

So in a Claude Code session you can just say:

> "Remember that I deploy with `ship.sh` now, not `deploy.sh`."
> *…later, in a fresh session…*
> "How do I deploy this project?"

and the answer comes from your own memory file. Corrections supersede the old fact instead of
duplicating it, so the memory stays clean over time.

### Automatic memory (opt-in)

One command wires Coldframe into Claude Code's hooks so memory happens *automatically* — you don't
have to tell it to remember, and you don't have to ask it to recall:

```bash
cold-frame hook install      # wires recall + capture hooks into ~/.claude/settings.json
cold-frame hook status       # check what's wired
```

- **Auto-recall** — a SessionStart hook injects your strongest durable memories at the top of each
  new session, so the agent opens already knowing you; a UserPromptSubmit hook adds memories relevant
  to the *current* prompt (gated on a real lexical match, so it adds signal, not per-turn noise).
- **Auto-capture** — a Stop hook enqueues each turn's transcript; the extraction runs on **Claude
  Code's own model** (via MCP sampling — no extra key) as the agent uses Coldframe, pulling out the
  durable facts you stated and dropping the chatter.
- **Per-project + global** — facts are tagged by **git project** (remote URL, else repo root), so a
  repo's conventions stay in that repo; clear personal facts ("I prefer…", "my name…") go to a global
  tier recalled everywhere. A new session recalls *this project ∪ global*.

> **Why it doesn't bloat (D26):** auto-capture funnels through the *same* engine as everything else —
> a salience pre-filter, then the durability gate (ephemeral dropped, low-confidence held for
> review), dedup (no duplicates), deterministic supersede (corrections replace, not pile up), and
> forgetting + per-scope caps. Automatic, but still *owned*: every auto-fact is visible and editable
> in the UI / Triage, nothing is captured opaquely.

> Works with any MCP client (not just Claude Code) — it's a standard stdio MCP server.

---

## As a Python library

```python
from datetime import UTC, datetime
from cold_frame import Memory, Scope

mem = Memory()                                   # ~/.cold-frame/memory.db, offline
mem.add("I switched jobs to Anthropic in 2026.", scope=Scope(user_id="coby"))

res = mem.search("where does coby work?", scope=Scope(user_id="coby"))
for hit in res.hits:
    print(hit.score, hit.note.content)

# rewind: what did I believe at a past point in time?
past = mem.search("where do I work?", as_of=datetime(2026, 3, 1, tzinfo=UTC))
```

---

## See it: the local web UI

```bash
cold-frame ui          # opens a read-only viewer at http://127.0.0.1:27182
```

A calm, dark dashboard of *what cold-frame knows about you now* — each fact's strength,
freshness, and how beliefs changed over time. Binds to localhost only.

---

## Quality: it rides on Claude Code, it doesn't call out

cold-frame's design choice: **don't run a separate LLM — borrow the host's.** The deterministic
engine (dedup bands, freshness, forgetting, the token-budget packer) does the heavy lifting with
**no key and no network**. When a write hits a genuinely *ambiguous* near-duplicate or possible
contradiction, cold-frame asks **the model Claude Code is already using** to judge it, via MCP
**sampling** — no second API key, no separate call you pay for twice. If the host doesn't support
sampling, those judgments simply fall back to the deterministic rules. So memory gets smarter when
embedded in a capable agent, and stays correct everywhere else.

The one thing that can't be borrowed this way is **embeddings** (semantic vectors). The built-in
offline embedder works out of the box; for sharper recall, plug in a **local** model
(`[local-llm]`, `sentence-transformers`) — still local, still no key. A cloud embedder
(`[openai]`) is the only option that needs a key, and it's entirely optional.

---

## Back up / move your memory

```bash
cold-frame export ~/cold-frame-backup.db     # a complete consistent snapshot
cold-frame import ~/cold-frame-backup.db     # restore it (your current DB is backed up first)
cold-frame export memory.ndjson --events     # or dump the append-only event log (portable)
```

A snapshot is a single self-contained file — copy it to another machine and `import` (or just
point `--db` at it). Import never touches the live WAL; it replaces the DB and keeps a
`.pre-import.bak` of what was there.

## Your data, your rules

- Everything is in **one file**: `~/.cold-frame/memory.db`. Copy it to back up, move it between
  machines, or delete it to start fresh. (`cold-frame doctor` shows its exact path.)
- Nothing is ever silently lost — forgetting/superseding **archives** facts (revivable, not deleted).
- **It forgets on its own, and stays bounded** — roughly every 20 new facts a consolidation pass
  runs automatically (decay-scoring, rolling up episodic clusters, archiving the weakest past
  per-scope caps), so the active set never grows without limit — no cron needed. Force it anytime
  with `cold-frame consolidate`, or run `cold-frame worker` to drain maintenance continuously.
- Logs are content-free by design (ids and counters only, never your note text).
- **Obvious secrets are blocked before they touch disk** — a deterministic scan (API keys, tokens,
  private keys, high-entropy blobs) drops them pre-write and reports a content-free placeholder; a
  blocked secret is never embedded, stored, or sent to the host model.
- **Anything you did store, you can scrub** — `cold-frame purge <id> --force` hard-removes a note
  from every grain (notes, search index, vectors, history, the event-log payload), VACUUMs, and
  grep-verifies the text is gone from the live DB. Full PII redaction and a crypto-shred/encrypted
  purge are planned — see Status.

---

## Status

The memory **engine** is built and tested end-to-end (skeleton → correctness → read-quality + UI →
forgetting → self-improving procedural memory → agentic self-edit), plus a local read-only web UI,
on a fully offline gate: `ruff` + `mypy --strict` + 278 deterministic mock-LLM tests green.

**Shipped:** the engine, the CLI, the MCP server, secret-blocking + a grep-verified hard-purge, an
embedder-swap re-index, and the read-only web UI (the thermal "memory field" + a fact inspector).

**Not in this version yet (planned):** full PII redaction + a crypto-shred / at-rest-encrypted purge
(v1 blocks obvious secrets and hard-purges on request, but doesn't redact PII or crypto-shred the
event log); the *write* web UI (triage/edit in the browser — for now, write via the CLI, MCP, or the
API); an idempotent event-log *replay* import (today backup/restore is snapshot-based; `--events`
dumps the log for inspection); and a PyPI release (the `cold-frame` name is pending trademark/registry
clearance). The design notes and the analysis of mem0 / Letta / Zep-Graphiti / Cognee / MemOS / A-MEM
/ LangMem that informed it live in [`docs/`](docs/).
