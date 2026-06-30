# PUBLIC API CONTRACT — Memory facade, Store ABC, Embedder/LLM ABCs, MCP tools

> where_it_goes: New focused doc: docs/api-contract.md (referenced from SPEC §3 "저장 레이어", §8 "Claude Code 연동", and §12 "디렉터리 구조"). It supersedes the loose signatures in analysis/design.md §3. SPEC §3 and §8 should each gain a one-line pointer: "구체 시그니처/계약 → api-contract.md".

# cold-frame — Public API Contract (build-ready)

> Goal: make the interfaces concrete enough that coding is mechanical. This pins (1) the `Memory` facade, (2) the `Store` ABC, (3) the `Embedder`/`LLM` ABCs, (4) the MCP tool contracts. Resolves audit findings: sync-vs-async, `correct_memory`/self-edit path-equivalence, `held_for_human`/quarantine surfacing, Store missing methods. Cites SPEC §3/§4/§5/§6/§8, decisions D8/D11/D15/D17/D18.

---

## 0. Foundational decision: SYNC, not async (v1)

**Decision: the entire v1 public surface is SYNCHRONOUS.** This overrides design.md §3 (`async def add/search/consolidate`).

Justification (local single-user tool):
1. There is exactly one process, one `.db`, one user. SQLite writes serialize at the file lock regardless; async buys no write concurrency.
2. The CLI (`cold-frame add`), the MCP stdio server, and the synchronous test harness ("LLM mock 결정적 단위테스트", SPEC §10) are all naturally blocking. Forcing `asyncio.run()` at three call sites adds ceremony and colored-function churn for zero throughput gain.
3. The only genuinely concurrent actor is the durable background worker (`forget/worker.py`, SPEC §6). It runs in a **separate thread** (or separate process invoked by `cold-frame consolidate`), polls the `jobs` table, and calls the same synchronous `Store`/`LLM`. Thread isolation, not async, is the concurrency model.
4. LLM/embedder network calls are slow but singular per `add`; we are not fanning out hundreds of concurrent requests. A blocking HTTP call inside a CLI invocation is fine.

Rules:
- All `Memory`, `Store`, `Embedder`, `LLM` methods are `def`, never `async def`.
- The MCP server (`mcp` SDK is async) wraps each sync core call with `anyio.to_thread.run_sync(...)` so a slow `add` doesn't block the MCP event loop. This is the **only** async seam, fully contained in `prompts/mcp.py`.
- If a `[server]` (FastAPI+Postgres) layer is ever built, it gets its own async `AsyncStore` adapter; the core stays sync. The `Store` ABC seam (D10) makes this a per-adapter choice, not a core rewrite.

---

## 1. Shared types (`cold_frame/models.py`)

All Pydantic v2 `BaseModel`. Timestamps are `datetime` (tz-aware UTC) in Python; the Store serializes to ISO8601-UTC TEXT (SPEC §1 이식성 규칙).

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

MemoryType = Literal["semantic", "episodic", "procedural"]
# CHANGED from design.md: Status gains "pending" to carry quarantine (audit: provenance invariant / D16).
Status = Literal["active", "archived", "deleted", "pending"]
#   active   : default, included in search
#   pending  : quarantined (provenance-less or confidence<0.4 held_for_human) — EXCLUDED from default search, visible in Triage
#   archived : soft-forgotten, excluded from search, revivable
#   deleted  : tombstone only (secret hard-purge; row retained as worthless marker, content scrubbed everywhere)

EdgeRelation = Literal["supersedes", "relates_to", "mentions", "derived_from", "caused_by"]
TriageReason = Literal["true_conflict", "ambiguous_merge", "low_confidence", "pin_adjacent_archive"]
UpdateType   = Literal["extract", "dedup", "conflict", "feedback", "manual", "correct", "consolidate"]

class Scope(BaseModel):
    user_id: str = "default"
    agent_id: str | None = None
    session_id: str | None = None

class Source(BaseModel):
    kind: Literal["message", "document", "tool", "manual"]
    ref: str
    role: str | None = None
    content_hash: str
    observed_at: datetime

class Note(BaseModel):
    id: str
    content: str
    memory_type: MemoryType
    keywords: list[str] = []
    tags: list[str] = []
    context: str = ""
    confidence: float = 1.0
    scope: Scope
    sources: list[Source] = []
    status: Status = "active"
    version: int = 1
    created_at: datetime
    expired_at: datetime | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    importance: float = 0.5
    last_accessed: datetime | None = None
    access_count: int = 0
    decay_S: float = 1.0
    # NEW columns (audit: held_for_human/triage + quarantine surfacing)
    held_for_human: bool = False
    triage_reason: TriageReason | None = None
    pinned: bool = False               # pin = ignore decay/band, top-fixed (SPEC §6)

class Edge(BaseModel):
    src_id: str
    dst_id: str
    relation: EdgeRelation
    weight: float = 1.0
    created_at: datetime
    valid_at: datetime | None = None
    invalid_at: datetime | None = None

class Signals(BaseModel):           # per-hit retrieval explainability (SPEC §5)
    semantic: float | None = None   # cosine
    bm25: float | None = None       # normalized
    edge: float | None = None       # 1-hop boost
    rrf: float                      # fused rank score
    rerank: float | None = None

class Hit(BaseModel):
    note: Note
    score: float                    # final fused/reranked score
    signals: Signals

class SearchResult(BaseModel):
    hits: list[Hit]
    used_tokens: int | None = None  # set only when token_budget given (SPEC §5 step 5)
    truncated: bool = False         # a hit's content was partially truncated to fit budget

class Strength(BaseModel):          # SPEC §6 §8.5 display strength — ONE canonical formula
    value: float                    # S ∈ [0,1]
    band: Literal["evergreen", "budding", "fading"]
    at_risk: bool                   # confidence<0.4 OR last_accessed>60d (SPEC §6)
    imminent: bool                  # archive-imminent sub-label: fading AND S<FADING_EMBER=0.10, excl. pinned
```

---

## 2. `Memory` facade (`cold_frame/api.py`)

The single public Python entrypoint. Construction:

```python
class Memory:
    def __init__(
        self,
        db_path: str | None = None,        # default ~/.cold-frame/memory.db; ":memory:" for tests
        *,
        embedder: "Embedder | None" = None, # default HashEmbedder (D4)
        llm: "LLM | None" = None,           # default None → naive extract (SPEC §4)
        default_scope: Scope | None = None, # default Scope(user_id="default")
        clock: "Clock | None" = None,       # injected time (G6); default SystemClock
        consolidate_every: int | None = None,            # auto-consolidate cadence (writes)
        pii_redact: "frozenset[PiiCategory] | None" = None,  # opt-in PII scrub (off by default)
        encryption_key: str | None = None,  # opt-in at-rest encryption ([crypto]); else $COLD_FRAME_KEY
    ) -> None: ...
    # On init: open Store, run Store.migrate() (idempotent), assert embedder dim matches DB
    # metadata (see Store.embedder_meta); if mismatch → raise EmbedderMismatchError.
```

### 2.1 Write methods

```python
def add(
    self,
    messages: list[Msg] | str,
    *,
    scope: Scope | None = None,        # falls back to default_scope
    infer: bool = True,                # False or llm=None → naive (1 message = 1 fact, SPEC §4)
    observed_at: datetime | None = None,
    source: Source | None = None,
    raw: bool = False,                 # verbatim store, skip extraction (CLI --raw)
) -> AddResult: ...
```
`Msg = TypedDict("Msg", {"role": str, "content": str})`.

```python
class AddResult(BaseModel):
    added: list[Note]
    superseded: list[str]              # ids archived by conflict
    deduped: list[str]                 # candidate ids merged-into-existing (no new row)
    blocked: list[BlockedSpan]         # secrets BLOCKed pre-disk (D15) — content NEVER included
    redacted: list[RedactedSpan]       # opt-in PII redacted pre-disk — content-free (category+count)
    held: list[Note]                   # active + held_for_human/quarantined (durability gate <0.4 or conflict)

class BlockedSpan(BaseModel):
    reason: Literal["secret", "credential"]
    placeholder: str                   # e.g. "[REDACTED:api_key]" — original span is discarded
```

**Invariants (D8 path-convergence):** `add()` routes EXTRACT → **WriteCore**(ADMISSION → DEDUP → CONFLICT → PERSIST), SPEC §4. Both `add()` and the self-edit tools (§2.4) call the identical `WriteCore.commit(candidates, *, scope, reinforce_dedup=True)`. There is exactly one persist path.

```python
def correct_memory(self, id: str, new_text: str, *, scope: Scope | None = None) -> CorrectResult: ...
class CorrectResult(BaseModel):
    archived: str                      # old note id (status→archived, invalid_at=now)
    new: Note                          # replacement, supersedes edge old←new

def update(self, id: str, **fields) -> Note: ...   # explicit field edit, version++, note_history snapshot (UpdateType="manual")
def delete(self, id: str) -> None: ...             # INTERNAL/secret-purge ONLY; user "forget" = archive (SPEC §9). Raises on active non-flagged note unless force=True
def pin(self, id: str) -> Note: ...                # pinned=True
def forget(self, id: str) -> Note: ...             # status→archived (non-destructive)
def revive(self, id: str) -> Note: ...             # status→active + decay re-spike (ux §8 G)
```

**`correct_memory` is a first-class WriteCore entry (audit finding):** it does NOT run similarity search. It directly: (a) loads `id`, (b) builds a new Note from `new_text` inheriting scope/type, (c) calls `WriteCore.commit_supersede(old=id, new=note, reason="correct")` which performs the SAME commit as the conflict path (old→archived + invalid_at=now + `supersedes` edge new→old + note_history snapshot UpdateType="correct"), keyed by explicit id rather than valid_at comparison. ADMISSION still runs on `new_text` (a correction can introduce a secret).

### 2.2 Read methods

```python
def search(
    self,
    query: str,
    *,
    scope: Scope | None = None,
    k: int = 10,
    token_budget: int | None = None,
    as_of: datetime | None = None,
    include_archived: bool = False,
    reinforce: bool = True,            # bump access/decay of surfaced hits (False on historical/MCP-merge reads)
) -> SearchResult: ...                 # default FILTER: status="active" AND NOT quarantined

def get(self, id: str) -> Note: ...                          # raises NoteNotFound
def strength(self, id: str) -> Strength: ...                 # canonical S (§4 below)
def neighbors(self, id: str, *, relations: list[EdgeRelation] | None = None) -> list[Edge]: ...
def fork_history(self, id: str) -> list[Note]: ...           # supersedes chain (belief-fork, ux Fact Detail)
```

### 2.3 Maintenance / forgetting (SPEC §6)

```python
def consolidate(self, *, scope: Scope | None = None, now: datetime | None = None) -> ConsolidateResult: ...
class ConsolidateResult(BaseModel):
    reinforced: int                    # decay_S adjustments
    merged: list[str]                  # episodic clusters → semantic summary note ids
    archived: list[str]                # soft-archived (score<threshold or cap)
    held_for_human: list[str]          # newly flagged triage items

def triage_queue(self, *, scope: Scope | None = None, limit: int = 50) -> list[TriageItem]: ...
class TriageItem(BaseModel):
    note: Note
    reason: TriageReason
    candidates: list[str] = []         # opposing/merge-candidate note ids
    impact: float                      # importance × recency, for ranked truncation (SPEC §6)

def resolve_triage(self, id: str, action: Literal["pin","let_go","merge","keep","supersede"],
                   *, target: str | None = None) -> None: ...
```

### 2.4 Self-edit / agentic tools (D8, P6 — audit: path-equivalence)

```python
def memory_tools(self, scope: Scope) -> list["ToolSpec"]: ...
```
Returns four tool specs whose handlers route through WriteCore. **Every one passes ADMISSION → DEDUP → CONFLICT → PERSIST** (P6 acceptance "tool path passes dedup/conflict" is now testable against these named handlers):

| tool | signature | WriteCore entry | gates applied |
|---|---|---|---|
| `create_fact(text, type?)` | new atomic fact | `commit(extract(text))` | ADMISSION(full) + DEDUP + CONFLICT |
| `update_fact(id, text)` | replace by id | `commit_supersede(id, ...)` | ADMISSION(full) + supersede |
| `supersede(old_id, text)` | explicit conflict | `commit_supersede(old_id, ...)` | ADMISSION(full) + supersede |
| `forget(id)` | archive | `archive(id)` | none (status-only, no content) |

**Gate-bypass rule (resolves deferred "per-source 추출 정책"):** No path bypasses ADMISSION. `raw=True` and direct agent writes skip the *extraction LLM* (verbatim content) but STILL run CLASSIFY→REDACT→CONFIDENCE-GATE. A secret asserted directly by an agent is BLOCKed exactly as one extracted from chat (D15 invariant "secret never touches disk" holds for all entries). The DURABILITY GATE (durable-only) is skipped for `raw`/explicit writes (the user/agent asserted intent), but quarantine still applies if no `source` is provided.

### 2.5 Procedural (D9, SPEC §7)

```python
def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult: ...
class ProceduralResult(BaseModel):
    name: str
    changed: bool                      # False if warrants_adjustment gate said no (drift guard)
    text: str                          # current procedural content
    version: int
def get_procedural(self, name: str) -> str: ...   # current behavior directive; "" if none
```

### 2.6 Exceptions (`cold_frame/exceptions.py`)

```python
class ColdFrameError(Exception): ...
class NoteNotFound(ColdFrameError): ...                # get/correct/update unknown id
class EmbedderMismatchError(ColdFrameError): ...       # configured embedder dim ≠ DB metadata (audit: cross-tier)
class SecretBlocked(ColdFrameError): ...               # raised only if caller used a strict mode demanding error-on-block; default add() just reports in AddResult.blocked
class VarHealerError(ColdFrameError): ...              # procedural f-string var dropped (SPEC §7 hard-fail)
class StoreError(ColdFrameError): ...                  # adapter-level (txn failure, migration)
```
Contract: `search/get` never raise on empty results (return empty `SearchResult`/raise only on bad id for `get`). `add` never raises on a blocked secret (reports in `AddResult.blocked`) — blocking is normal flow, not an error.

---

## 3. `Store` ABC (`cold_frame/store/base.py`)

The adapter seam (D10, "신성불가침"). SQLiteStore is the v1 impl; PostgresStore later behind the identical contract. **All methods synchronous.** SoT + vector + FTS are written in ONE transaction (SPEC §3 dual-write, no drift).

```python
class Store(ABC):
    # --- lifecycle ---
    @abstractmethod
    def migrate(self) -> None: ...
        # idempotent; creates notes/note_fts/note_vec/edges/note_history/sources/access_log/jobs/meta.
        # Writes embedder_meta on first run if absent.

    @abstractmethod
    def embedder_meta(self) -> "EmbedderMeta | None": ...
        # returns {embedder_id, dim} stored in a `meta` table; None on fresh db. (audit: dim handling)
    @abstractmethod
    def set_embedder_meta(self, meta: "EmbedderMeta") -> None: ...

    # --- atomic write (ALL grains in one txn) ---
    @abstractmethod
    def add_note(self, note: Note, emb: "list[float] | None") -> None: ...
        # INSERT notes + note_fts + note_vec(if emb) + sources rows + note_history(v1 snapshot)
        # + the co-written event-log row (see §3.1) — ONE transaction. emb=None allowed (pending/no-embed).
    @abstractmethod
    def update_note(self, note: Note, *, update_type: UpdateType, emb: "list[float] | None" = None) -> None: ...
        # version++ semantics handled by caller; writes note_history snapshot + event-log row.
    @abstractmethod
    def get_notes(self, ids: list[str]) -> list[Note]: ...
    @abstractmethod
    def set_status(self, id: str, status: Status, *, invalid_at: datetime | None = None) -> None: ...

    # --- retrieval ---
    @abstractmethod
    def knn(self, emb: list[float], k: int, *, scope: Scope, statuses: list[Status],
            as_of: datetime | None = None) -> list[tuple[str, float]]: ...   # [(note_id, cosine)]
    @abstractmethod
    def bm25(self, query: str, k: int, *, scope: Scope, statuses: list[Status],
             as_of: datetime | None = None) -> list[tuple[str, float]]: ...  # [(note_id, raw_bm25)]
    @abstractmethod
    def touch(self, ids: list[str], *, now: datetime) -> None: ...
        # REINFORCE: access_count++, last_accessed=now, decay_S++ AND insert capped access_log row(s) (§3.2)

    # --- edges ---
    @abstractmethod
    def add_edge(self, edge: Edge) -> None: ...
    @abstractmethod
    def neighbors(self, ids: list[str], *, relations: list[str] | None = None) -> list[Edge]: ...

    # --- triage / quarantine reads (audit: missing Store methods) ---
    @abstractmethod
    def held_for_human(self, *, scope: Scope, limit: int) -> list[Note]: ...   # status="pending" OR held_for_human=True
    @abstractmethod
    def by_status(self, *, scope: Scope, status: Status, sort: Literal["decay","recent","importance"],
                  limit: int, offset: int = 0) -> list[Note]: ...

    # --- jobs (durable queue, SPEC §6) ---
    @abstractmethod
    def enqueue(self, kind: str, payload: dict, *, run_after: datetime | None = None) -> str: ...
    @abstractmethod
    def claim_job(self, *, now: datetime) -> "Job | None": ...   # SELECT ... locked_at=now, attempts++
    @abstractmethod
    def complete_job(self, id: str) -> None: ...
    @abstractmethod
    def fail_job(self, id: str, *, retry_after: datetime | None) -> None: ...

    # --- event log / export (D17, audit critical) ---
    @abstractmethod
    def append_event(self, ev: "Event") -> None: ...   # called INSIDE add_note/update_note txn
    @abstractmethod
    def iter_events(self, *, since_hlc: str | None = None) -> "Iterator[Event]": ...
    @abstractmethod
    def purge_note(self, id: str) -> "PurgeReport": ...  # secret hard-purge across ALL grains (§3.3)
    @abstractmethod
    def in_transaction(self) -> "ContextManager[None]": ...  # explicit txn for WriteCore multi-step commits
```

Error semantics: every Store method raises `StoreError` (wrapping the driver exception) on failure; partial writes are impossible because each method is one transaction (or participates in the caller's `in_transaction()`). `knn`/`bm25` return `[]` (never raise) on no match. `get_notes` skips unknown ids silently.

### 3.1 Event log — resolves D17 contradiction (notes = SoT, event log = co-written audit/sync log)

**Decision (resolves audit critical #1): option (c) — notes table is the source of truth; the event log is a co-written, append-only audit/sync log written in the SAME transaction.** Drop "materialized view" language from SPEC §3. The notes table is NOT a SQL VIEW and is NOT trigger-rebuilt; §4 PERSIST mutates it in place (consistent with the rest of SPEC). The event log is a parallel ledger enabling export/backup/future-sync (D17), not the primary read path.

```sql
CREATE TABLE events (
  event_id   TEXT PRIMARY KEY,        -- uuid4
  device_id  TEXT NOT NULL,           -- per-install id (meta table)
  hlc        TEXT NOT NULL,           -- hybrid logical clock, lexically sortable
  op_type    TEXT NOT NULL,           -- note.add | note.update | note.archive | note.purge | edge.add ...
  entity_id  TEXT NOT NULL,           -- note_id or edge key
  content_hash TEXT,                  -- hash of payload for dedup/audit; NULL for purge tombstone
  payload    TEXT,                    -- json of the change; NULL/scrubbed for secret purge
  ts         TEXT NOT NULL
);
CREATE INDEX idx_events_hlc ON events(hlc);
```
`export` = `iter_events()` dump (§ bundle spec, separate doc). FTS/vec stay consistent because they are written in the SAME `add_note` transaction as notes AND the event row — there is no async materialization to drift.

### 3.2 access_log — resolves audit (DDL + write path + retention)

```sql
CREATE TABLE access_log (note_id TEXT, ts TEXT);
CREATE INDEX idx_access_note ON access_log(note_id, ts);
```
- **Write path:** `Store.touch()` (called by SPEC §5 step 6 REINFORCE) inserts one row per returned note per search.
- **Retention (resolves R5 unbounded-growth):** `touch()` keeps at most **N=50 most-recent rows per note** (delete older within the same txn: `DELETE ... WHERE note_id=? AND ts NOT IN (SELECT ts ... ORDER BY ts DESC LIMIT 50)`). The forgetting-curve sparkline (ux §8.5) downsamples these 50 points. If the table is absent (old db), strength degrades to current `decay_S`/`last_accessed` only (SPEC §2 graceful degrade).

### 3.3 Secret hard-purge — resolves audit critical #2 (purge invariant)

`purge_note(id)` is the ONLY operation that violates append-only. It scrubs, in one transaction:
1. `notes`: content/keywords/tags/context → empty, status="deleted" (worthless tombstone retained for HLC continuity).
2. `note_fts`: delete the FTS5 row (and run `INSERT INTO note_fts(note_fts) VALUES('rebuild')` is too heavy — instead delete-by-rowid which removes shadow content).
3. `note_vec`: delete the vector row.
4. `note_history`: delete all snapshots for `id`.
5. `sources`: delete rows for `note_id`.
6. `events`: rewrite all prior events for `entity_id=id` to payload=NULL, content_hash=NULL, op_type retained as `note.purge` tombstone (preserves HLC ordering; defeats append-only retention of the value).
7. `jobs`: delete any pending job whose payload references `id`.
8. Then: `PRAGMA wal_checkpoint(TRUNCATE)` → `PRAGMA secure_delete=ON` was set at connect → `VACUUM`. Post-purge grep-verification (D16) scans the file for the placeholder/hash.

**Relationship to D15 BLOCK (resolves the BLOCK-vs-purge ambiguity):** D15 admission BLOCK is the primary defense — a detected secret never reaches `add_note`, so the ideal residue set is EMPTY. `purge_note` exists ONLY for **late-detected** secrets/PII (detector miss surfaced later, or user-flagged). It is not dead code; it is the second line. **If SQLCipher (D16 opt-in) is on, step 8's grep-verification can't byte-scan ciphertext, so v1 verifies the LOGICAL scrub through the keyed (decrypting) connection instead — the needle must be absent from live `notes.content`/`events.payload`/`jobs.payload` (`_content_clean`). Crypto-shred (key-destruction) is the DEFERRED v1.1 variant, not what v1 does.** Export bundles: a purged note's events carry NULL payload, so export never leaks it.

---

## 4. Canonical strength & archive formulas (resolves audit high: 3-vs-4 bands, divergent formulas)

**ONE display-strength formula (SPEC §6 §8.5, authoritative):**
```
retrievability = exp(-Δt_last_accessed / decay_S)          # ∈ [0,1]
S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))
```
**Three bands (drop the 4th ember band from ux §8.2; 0.10 is a sub-label, not a band):**
```
S ≥ 0.66            → evergreen 🌳
0.33 ≤ S < 0.66     → budding   🌿
S < 0.33            → fading     🌱
at_risk (○ overlay, band-independent): confidence < 0.4 OR (now − last_accessed) > 60d
```
**ONE archive-score (SPEC §6 step 1, used ONLY by consolidation, never for display):**
```
archive_score = w_r·exp(-Δt/decay_S) + w_i·importance + w_rel·relevance
                w_r=0.5, w_i=0.3, w_rel=0.2  (relevance=0 when no active query context)
```
**Coupling rule (resolves the "🌳 while archive-imminent" contradiction):** archive may fire ONLY when `S < 0.33` (the fading floor) AND `archive_score < ARCHIVE_THRESHOLD=0.20`, OR on capacity cap. Therefore a note in the evergreen/budding band can NEVER be archived — band glyph and archive decision cannot disagree. ux §8.2's "0.10 ember / archive-imminent" maps to `S < 0.10` shown as a fading sub-state, not a separate band. The Fact-Detail `.42/.30/.18` weights in ux §4.3 are SUPERSEDED by `archive_score` above; ux §4.3 should be updated to reference this section.
**Capacity cap concrete numbers (resolves "type별 N" unspecified):** per scope, active-note caps — `semantic=2000`, `episodic=500`, `procedural=100`. Over cap → archive lowest `archive_score` first (deterministic, SPEC §6 step 4).

---

## 5. `Embedder` ABC (`cold_frame/llm/base.py`)

```python
class EmbedderMeta(BaseModel):
    embedder_id: str       # "hash" | "openai:text-embedding-3-small" | "local:bge-small" ...
    dim: int

class Embedder(ABC):
    @property
    @abstractmethod
    def meta(self) -> EmbedderMeta: ...        # id + dim; stored in DB on first run (audit: dim handling)
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...   # batch; returns dim-length vectors, L2-normalizable
    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
```
- **HashEmbedder (default, D4):** deterministic, deps=0. `meta = EmbedderMeta("hash", dim=256)`. (Fixes design.md DDL hardcoding `FLOAT[1536]` — the vec0 dim is `meta.dim`, written at migrate time, NOT a literal.)
- **Numpy-KNN default vs sqlite-vec coexistence (audit):** default storage = `note_vec(note_id TEXT, embedding BLOB)` (numpy `float32` bytes); `Store.knn` does brute-force cosine. With `[vec]` extra, SQLiteStore creates `vec0(note_id, embedding FLOAT[{meta.dim}])` instead — dim comes from `meta`, never hardcoded. Both behind the same `knn()` contract.
- **Cross-tier re-embedding migration (resolves deferred P0 blocker):** on `Memory.__init__`, if `store.embedder_meta() != embedder.meta`, raise `EmbedderMismatchError` UNLESS `config.allow_reembed=True`, in which case enqueue a `reembed` job (background worker re-embeds all notes with the new embedder, updates `note_vec` + `meta`) and BLOCK cross-embedder KNN until done (notes carry an implicit "stale-vector" state = `note_vec` row absent → excluded from semantic fan-out, BM25 still works). Never mix dims in one KNN call.

## 6. `LLM` ABC (`cold_frame/llm/base.py`)

```python
class LLM(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, *, json_schema: dict | None = None,
                 temperature: float = 0.0) -> str: ...      # returns text or JSON string if schema given
    @property
    @abstractmethod
    def is_local(self) -> bool: ...                          # True for Ollama/llama.cpp; False for OpenAI/Anthropic
```
**Admission-LLM locality invariant (resolves audit low, enforces D4/R11):** SPEC §4's admission tie-break LLM and any pre-persist secret-span evaluation MUST use an `LLM` where `is_local is True`, **regardless of the configured extraction provider**. WriteCore asserts this: if the admission gate needs LLM tie-break and no local LLM is configured, it falls back to deterministic regex/entropy classification (BLOCK on ambiguity = fail-closed) and NEVER sends the candidate span to a remote endpoint. Testable invariant: a test configures a remote `LLM` mock that records calls and asserts zero calls with secret-bearing spans.

Providers (`cold_frame/llm/providers.py`): `HashEmbedder`, `OpenAIEmbedder`, `LocalEmbedder`; `OpenAILLM`, `AnthropicLLM`, `OllamaLLM`. (Note: cold-frame is a Claude-native product per D19 — `AnthropicLLM` is the recommended cloud LLM; default ships `llm=None` → naive extract, no key needed.)

---

## 7. MCP tool contracts (`cold_frame/prompts/mcp.py`, SPEC §8)

Server id `cold-frame`, transport stdio (local, OAuth-free, D11). Built on the official `mcp` Python SDK (`FastMCP`). Each tool handler wraps a sync `Memory` call via `anyio.to_thread.run_sync`. **Every tool result includes a `ui` deep-link** `http://localhost:27182/fact/{id}` (SPEC §8).

### 7.1 `search_memory`
Input: `{query: string, k?: int=8, scope?: string, as_of?: string(ISO8601), token_budget?: int}`
Output:
```json
{"hits":[{"id":"...","content":"...","memory_type":"semantic","confidence":0.9,
          "strength":0.72,"band":"evergreen","status":"active",
          "sources":[{"kind":"message","ref":"...","observed_at":"..."}],
          "supersedes":["old_id"],"ui":"http://localhost:27182/fact/..."}],
 "used":1840}
```
`strength`/`band` from §4. `scope` parsed as `"user:coby"`/`"user:coby,session:abc"`. Errors → MCP error with `{"code":"invalid_scope"|"internal","message":...}`. Empty result = `{"hits":[],"used":0}` (not an error).

### 7.2 `add_memory`
Input: `{text: string, type?: "semantic"|"episodic"|"procedural", source?: string}`
Output: `{"added":[{"id":"...","content":"..."}],"superseded":["id"],"deduped":["id"],"blocked":["secret"],"held":["id"],"ui":"http://localhost:27182/fact/{first_added_id}"}`
`blocked` carries only the reason string, never the secret content (D15). If everything was blocked/deduped, `added=[]` is a normal success.

### 7.3 `summarize`
Input: `{topic?: string, scope?: string, as_of?: string}`
Output: `{"summary":"...","fact_ids":["..."],"ui":"http://localhost:27182/search?q={topic}"}`
Gathers related facts (search topic, k=20) → LLM summarize. If `llm is None`, returns a concatenated bullet list of the facts (offline graceful). 

### 7.4 `correct_memory`
Input: `{id: string, new_text: string}`
Output: `{"archived":"old_id","new":{"id":"...","content":"..."},"ui":"http://localhost:27182/fact/{new_id}"}`
Routes to `Memory.correct_memory` (§2.1). Unknown id → MCP error `{"code":"not_found"}`. Maps to the bi-temporal in-place supersede (SPEC §8).

### 7.5 Resources (read-only, @mention-able)
- `cold-frame://fact/{id}` → JSON of the Note + sources + 1-hop edges + belief history.
- `cold-frame://recent` → list of recent active notes (Home feed).
Implemented via `@mcp.resource(...)`; both call `Memory.get`/`by_status` on a worker thread.

### 7.6 Server lifecycle / errors
- Single `Memory(db_path=~/.cold-frame/memory.db)` constructed at server start; reused across tool calls (one open Store, WAL).
- All tool handlers catch `ColdFrameError` → MCP error response with a stable `code`; unexpected exceptions → `{"code":"internal"}` (no stack/content leak).
- `add_memory` blocking a secret is a SUCCESS response (reason in `blocked`), never an MCP error — consistent with §2.6.

---

## 8. UI read-API (P3, `[ui]` extra; ux §5.2 / §6.2) — for completeness of the public contract

Thin read-mostly JSON over the same `Memory`, served by `cold-frame ui` (localhost:27182). All GET, all derive from single-file SELECTs:
```
GET /api/facts?scope=&status=active&sort=decay|recent|importance&limit=&offset=   → [Note + strength]
GET /api/fact/:id            → Note + sources + note_history + 1-hop edges (Fact Detail join)
GET /api/fact/:id?as_of=ISO  → as-of snapshot (temporal filter, belief-state)
GET /api/forks/:id           → supersedes-based fork list (belief history)
GET /api/ledger              → consolidation ledger (jobs + derived_from edges)
GET /api/ledger/:jobId       → one job's diff
GET /api/triage?scope=       → held_for_human items (Triage queue)
POST /api/triage/:id/resolve {action,target?}   → resolve_triage (the ONLY mutating UI endpoint)
```
Mirrors `Memory` methods 1:1 so the UI never reaches into the Store directly.

---

## 9. Naming caveat (audit: D19 vs D-P2 unresolved)

This contract hardcodes `cold-frame` (package), `cold_frame` (import), `~/.cold-frame/memory.db`, MCP id `cold-frame`, scheme `cold-frame://`, per D19 (✅). **Blocked external dependency:** D-P2 (✅, contradictory) recommends abandoning `cold-frame`; PyPI/trademark clearance is unverified. Implementation rule: route the literal name through a single `cold_frame.branding` module (`PKG`, `DB_DIR`, `MCP_ID`, `URL_SCHEME`, `UI_PORT=27182`) so a rename is one-file, not a sweep — do NOT publish to PyPI until D19-vs-D-P2 is resolved (see open questions).
