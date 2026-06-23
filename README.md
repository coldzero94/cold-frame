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

```bash
pip install "cold-frame[mcp]"     # core + the Claude Code / MCP server
```

The core depends only on `pydantic` + `numpy`. Everything heavier (the MCP SDK, cloud LLMs,
the web UI) is an opt-in extra, so a plain install stays tiny.

*(From a source checkout: `uv sync --extra mcp`, then prefix the commands below with `uv run`.)*

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

This is the point. Register cold-frame once and Claude Code gains persistent memory across
sessions:

```bash
claude mcp add cold-frame -- cold-frame mcp
```

Now the agent has six memory tools:

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

## Your data, your rules

- Everything is in **one file**: `~/.cold-frame/memory.db`. Copy it to back up, move it between
  machines, or delete it to start fresh. (`cold-frame doctor` shows its exact path.)
- Nothing is ever silently lost — forgetting/superseding **archives** (revivable); only an
  explicit secret/PII purge deletes.
- Secrets are blocked before they ever touch disk, and note content is never written to logs.

---

## Status

The full engine is built and tested (skeleton → correctness → read-quality + UI → forgetting →
self-improving procedural memory → agentic self-edit). The design notes and the analysis of
mem0 / Letta / Zep-Graphiti / Cognee / MemOS / A-MEM / LangMem that informed it live in
[`docs/`](docs/).
