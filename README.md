# cold-frame

A **local-first, ownable** memory layer for LLM agents. One SQLite file holds everything
(facts + BM25 index + vectors + edges + version history + provenance). No server, no API key
required to start — install and it runs.

> Design philosophy and the code-level analysis of mem0 / Letta / Zep-Graphiti / Cognee / MemOS /
> A-MEM / LangMem that informed it live in [`docs/`](docs/). This is **P1** (skeleton): a working
> `add` / `search` core. Dedup, bi-temporal conflict resolution, the token-budget packer, the
> forgetting/consolidation engine, and procedural memory land in P2–P6 (see `docs/design.md`).

## Install (simple, local)

```bash
pip install cold-frame            # core: SQLite, offline default, CLI + library
pip install cold-frame[openai]    # cloud embeddings/LLM (better quality)
pip install cold-frame[local-llm] # fully offline real embeddings (sentence-transformers)
pip install cold-frame[server]    # the SEPARATE product layer (Postgres + FastAPI)
```

The core install pulls only `pydantic` + `numpy`. Server/Postgres code is decoupled and never
imported by the local engine.

## Use

```python
from cold_frame import Memory, Scope

mem = Memory()                                   # ~/.cold-frame/memory.db, offline HashEmbedder
mem.add("I switched jobs to Anthropic in 2026.", scope=Scope(user_id="coby"))
res = mem.search("where does coby work?", scope=Scope(user_id="coby"), k=5)
for hit in res.hits:
    print(hit.score, hit.note.content)
```

For real extraction quality, pass a real LLM/embedder:

```python
from cold_frame import Memory
from cold_frame.llm import OpenAIEmbedder, OpenAILLM
mem = Memory(embedder=OpenAIEmbedder(), llm=OpenAILLM())
```

## CLI

```bash
cold-frame add "I prefer dark roast coffee."
cold-frame search "coffee preference"
cold-frame path           # prints the db file location
```

Your data (`~/.cold-frame/memory.db`) is separate from the program: upgrade/reinstall freely,
back up by copying the file.
