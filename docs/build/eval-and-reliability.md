# EVAL HARNESS + RELIABILITY (SPEC §10 expansion + new §13 Reliability/Observability + new §14 Performance Budget)

> ⚠️ SUPERSEDED — code wins (see `cold_frame/llm/base.py`, `store/base.py`, `constants.py`, `cold_frame/eval/`); pinned pre-build, not re-synced. Where this doc disagrees with shipped code, code is authoritative (CLAUDE.md §1). Carets (^) below flag the worst-drifted sections.

> where_it_goes: Replace/expand SPEC.md §10 with the concrete harness spec below; add two new SPEC sections (§13 Reliability & Failure Modes, §14 Performance Budget) — or a focused doc docs/eval-and-reliability.md cross-linked from SPEC §10/§11. The LLM/Embedder ABC defined in §A is the prerequisite that also belongs in cold_frame/llm/base.py and should be reflected in SPEC §1/§4.


# Eval Harness + Reliability — Build-Ready Spec

This deepens SPEC §10 (Eval) and fills the missing reliability/observability/performance specs. It is written so P1 can implement mechanically. Code/schemas/prompts in English; prose Korean OK.

---

## A. Prerequisite: the LLM/Embedder ABC (the deterministic mock seam)

> ^ SUPERSEDED — code wins (`cold_frame/llm/base.py`). Shipped `LLM.complete` is SYNC `def` (I4 — the only `async def` is in `cold_frame/mcp.py`), not `async def`. `TaskTag` is a `StrEnum` and does NOT include `admission_tiebreak`; the `assert_local_for` / `LOCAL_ONLY_TASKS` / invariant I-LOCAL below were REMOVED (ADR-I7-cut, 2026-07-01) — the admission path makes zero LLM calls.

The whole "LLM mock으로 결정적" 철칙 (SPEC §10, R16) is unbuildable until the LLM seam is pinned. Neither SPEC nor design.md gives the ABC signature. Pin it now (lives in `cold_frame/llm/base.py`).

```python
# cold_frame/llm/base.py
from typing import Protocol, TypeVar, Type
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLM(Protocol):
    name: str            # "openai:gpt-4o-mini", "ollama:llama3.1", "mock"
    is_local: bool       # True for ollama/llama.cpp/mock; False for openai/anthropic

    async def complete(
        self,
        *,
        task: str,              # ENUM tag, see TaskTag below — REQUIRED, drives mock dispatch + logging
        system: str,
        user: str,
        schema: Type[T] | None = None,   # if set, provider does structured/JSON output → parsed into T
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> "LLMResult[T]": ...

class LLMResult(BaseModel):
    parsed: BaseModel | None     # populated when schema given
    text: str                    # raw text (schema=None path)
    usage: Usage                 # prompt_tokens, completion_tokens
    model: str

class Embedder(Protocol):
    name: str        # "hash", "openai:text-embedding-3-small", "bge-small"
    dim: int         # property — drives the per-embedder dimension fix (audit finding)
    is_local: bool
    def embed(self, texts: list[str]) -> "np.ndarray":   # shape (n, dim), float32, L2-normalized
        ...
```

`TaskTag` is a closed enum — every LLM call in the engine MUST pass one. It is the mock dispatch key, the per-task local-only enforcement key, and the log key:

```python
TaskTag = Literal[
    "extract",            # write/extract.py     — chat → candidate facts
    "admission_tiebreak", # write/admission       — ambiguous secret/PII span  (MUST be local — invariant I-LOCAL)
    "dedup_batch",        # write/dedup.py        — ambiguous near-dup batch
    "conflict_judge",     # write/conflict.py     — dual-candidate dup-vs-contradiction
    "consolidate_summary",# forget/consolidate.py — episodic cluster → semantic summary
    "rerank_judge",       # read/rerank.py        — LLM-boolean rerank (API path)
    "gradient_diagnose",  # procedural/optimize.py
    "gradient_edit",      # procedural/optimize.py
]
```

**Invariant I-LOCAL (testable, resolves audit "admission LLM strictly local"):** the engine MUST refuse a non-local `LLM` for `task in {"admission_tiebreak"}` (and any pre-persist secret-span eval). Enforced in one place:

```python
def assert_local_for(task: TaskTag, llm: LLM):
    if task in LOCAL_ONLY_TASKS and not llm.is_local:
        raise PolicyError(f"task={task} requires a local LLM (D4/R11); got {llm.name}")
LOCAL_ONLY_TASKS = {"admission_tiebreak"}
```
Eval case `test_admission_tiebreak_rejects_remote_llm` asserts this raises.

---

## B. Eval Harness

### B.1 Golden dataset format

One file per scenario family under `cold_frame/eval/datasets/`. Format = **YAML** (human-diffable, comments) with a strict pydantic schema. JSON accepted for machine-generated (LoCoMo adapter). A case file:

```yaml
# cold_frame/eval/datasets/freshness.yaml
suite: freshness
embedder: hash          # which embedder the harness must configure (default deterministic)
cases:
  - id: fresh-job-switch
    description: "works at X then switched to Y → X archived, Y active, as_of returns old"
    seed: 42            # for any nondeterministic tiebreak
    # ---- mock LLM script: keyed by (task, match) → canned structured response ----
    llm_script:
      - task: extract
        match: {contains: "Vessl"}
        returns:
          facts:
            - content: "works at Vessl"
              memory_type: episodic
              valid_at: "2026-01-01T00:00:00Z"
              importance: 0.6
      - task: extract
        match: {contains: "Anthropic"}
        returns:
          facts:
            - content: "works at Anthropic"
              memory_type: episodic
              valid_at: "2026-06-01T00:00:00Z"
              importance: 0.6
      - task: conflict_judge
        match: {any: true}
        returns: {relation: "contradiction", same_subject: true}
    # ---- the actual scenario as ordered ops ----
    steps:
      - op: add
        at: "2026-01-02T09:00:00Z"        # injected clock (now())
        scope: {user_id: alice}
        text: "I work at Vessl"
      - op: add
        at: "2026-06-02T09:00:00Z"
        scope: {user_id: alice}
        text: "I just joined Anthropic"
    # ---- assertions ----
    expect:
      notes:
        - where: {content_like: "Anthropic"}
          status: active
        - where: {content_like: "Vessl"}
          status: archived
          invalid_at: "2026-06-01T00:00:00Z"
      edges:
        - {relation: supersedes, src_like: "Anthropic", dst_like: "Vessl"}
      search:
        - query: "where do I work"
          scope: {user_id: alice}
          as_of: "2026-03-01T00:00:00Z"
          expect_top_content_like: "Vessl"
        - query: "where do I work"
          scope: {user_id: alice}
          expect_top_content_like: "Anthropic"
```

Pydantic schema (in `eval/harness.py`):

```python
class LlmScriptEntry(BaseModel):
    task: TaskTag
    match: dict          # {contains: str} | {any: true} | {seq: int}  matched against the rendered user prompt
    returns: dict        # parsed straight into the call's `schema` model; harness validates it fits
class Step(BaseModel):
    op: Literal["add","search","consolidate","correct","tick","forget","revive","pin"]
    at: datetime | None  # sets the injected clock; if None, clock unchanged
    scope: Scope | None
    text: str | None
    # op-specific extras (id, new_text, query, k, budget, as_of, scope, topic)
    extra: dict = {}
class ExpectNote(BaseModel):
    where: dict          # {content_like|id|content}
    status: Status | None; invalid_at: datetime | None; held_for_human: bool | None; version: int | None
class Case(BaseModel):
    id: str; description: str; seed: int = 0
    embedder: str = "hash"
    llm_script: list[LlmScriptEntry] = []
    steps: list[Step]
    expect: ExpectBlock
class Suite(BaseModel):
    suite: str; embedder: str = "hash"; cases: list[Case]
```

### B.2 How the LLM is mocked deterministically

> ^ SUPERSEDED — the shipped `ScriptedLLM.complete` is SYNC `def` (matches the sync `LLM` ABC, I4), not `async def` as written below.

`ScriptedLLM(LLM)` is the mock. It is **stateful, ordered-with-fallback**:

```python
class ScriptedLLM:
    name = "mock"; is_local = True
    def __init__(self, script: list[LlmScriptEntry]):
        self._script = list(script); self._cursor = 0; self.calls: list[dict] = []
    async def complete(self, *, task, system, user, schema=None, **kw):
        self.calls.append({"task": task, "user": user})          # for assertion / leak tests
        entry = self._pick(task, user)                            # 1) first unconsumed entry whose task==task and match(user) → consume
        if entry is None:
            raise EvalError(f"no scripted response for task={task} user={user[:80]!r}")
        if schema is not None:
            return LLMResult(parsed=schema.model_validate(entry.returns), text="", usage=Usage(0,0), model="mock")
        return LLMResult(parsed=None, text=entry.returns["text"], usage=Usage(0,0), model="mock")
```

Matching: `{contains: s}` → `s in user`; `{seq: n}` → the n-th call regardless of content; `{any: true}` → match any (lowest priority, reusable). Specific matches consume; `any:true` entries are reusable. Unmatched call ⇒ hard error (forces tests to declare every LLM interaction — this is the discipline A-MEM/langmem skipped).

**Determinism rules the harness enforces:**
1. `Embedder` default = `HashEmbedder` (seeded, no network). `dim` small (e.g. 256) and stable. Hash = blake2b(token)→buckets, L2-normalize → cosine is deterministic and meaningful enough for "pizza"~"pizza" vs "pizza"≠"pasta" dedup cases.
2. **Injected clock**: engine never calls `datetime.utcnow()` directly — it calls `clock.now()` from a `Clock` protocol. Harness supplies `FrozenClock` driven by `step.at`. (Code change: thread a `clock` through `Memory`/WriteCore/forget — small but mandatory for freshness/forgetting determinism.)
3. **Injected RNG**: any tiebreak randomness seeded by `case.seed`.
4. `uuid` for note ids: in eval mode, ids are `uuid5(NAMESPACE, f"{case.id}:{ordinal}")` so assertions can reference them and snapshots are stable.

### B.3 Metric computation (per suite)

| suite | metric | computation |
|---|---|---|
| extraction | precision, recall, F1 over fact set | match predicted↔gold by **normalized cosine ≥ 0.9 OR exact normalized string**; greedy 1:1 max-matching; P=matched/predicted, R=matched/gold |
| dedup | merge-correct rate, over-merge rate | positives must collapse to 1 note; negatives must stay N notes; report (#correct-merge / #pos), (#wrong-merge / #neg) |
| freshness | pass/fail per assertion | exact: status, invalid_at, supersedes edge, as_of top-hit content_like |
| forgetting | retained-correct, archived-correct | after `consolidate`: pinned/high stay active; low beyond cap archived (not deleted — assert row still present, status=archived) |
| precision@k | P@k, MRR | expected note ids vs returned top-k ids |
| cross-scope | leak count (must be 0) | search in scope B returns 0 notes owned only by A |
| token_budget | budget-respected (bool), top-included (bool) | `sum(token_len(hit.content_packed)) ≤ budget` AND highest-strength note present |

Token length = the same tokenizer the budget packer uses (SPEC §5 BUDGET). Default tokenizer = `tiktoken cl100k_base` if installed, else a deterministic whitespace/4-char heuristic; **harness and packer MUST use the same one** (config `token_counter`), else budget eval is meaningless.

`harness.run_suite(path) -> SuiteReport{passed, failed, metrics, failures:[{case_id, assertion, got, want}]}`.

### B.4 CI wiring

- `cold_frame/eval/harness.py` exposes `pytest` params: `@pytest.mark.parametrize` over every case in `datasets/*.yaml` → one test per case (granular failures).
- Two CI tiers:
  1. **`tests-core`** (every PR, no network, no keys): all mock-LLM + HashEmbedder suites. Must be green to merge. This is the R16/R17 gate.
  2. **`evals-live`** (nightly / manual, needs keys): LoCoMo/LongMemEval adapters + real embedder regression. Reports metric deltas, non-blocking.
- Phase gates map to suites (resolves "P-acceptance is untestable"):
  - P1 accept = extraction + precision@k + cross-scope green.
  - P2 accept = dedup + freshness green.
  - P3 accept = token_budget green.
  - P4 accept = forgetting green + the unbounded-growth assertion (B.5).
  - P6 accept = run dedup+freshness suites **through the self-edit tool path** (same cases, `op` routed via `memory_tools` instead of `add`) — proves single-WriteCore.

### B.5 Anti-regression assertions (cheap, high-value)

- `test_no_unbounded_growth` (R5): add 10k low-importance episodic in scope X, run consolidate, assert `count(status='active', type='episodic') <= capacity_cap`.
- `test_purge_leaves_no_residue` (D16/D-T5): add a fact containing a scripted secret marker, purge, then grep across `notes, note_fts (+ shadow tables), note_vec, note_history, sources, jobs.payload, events` for the marker → 0 hits. (This is the testable form of the purge invariant; the purge mechanism itself is owned by the schema/privacy spec, but the eval lives here.)
- `test_no_secret_to_remote` (I-LOCAL): configure a `RecordingRemoteLLM` (is_local=False) as extraction provider; feed a secret-bearing turn; assert it was BLOCKed pre-disk AND `recording.calls` contains no secret span for any task. 

---

## C. Reliability / Failure-Mode Spec

### C.1 The `jobs` durable queue (concrete)

> ^ SUPERSEDED on two details — code wins (`cold_frame/store/base.py`, `store/_ddl.py`, `constants.py`): (1) the in-flight status is `'running'` with a `locked_by` worker column, NOT `'leased'`/`lease_owner`; (2) backoff is `RETRY_BACKOFF_BASE(0.05)·2^attempts` seconds, UNCAPPED — not `min(2^attempts, 3600)s`. Job `kind`s that actually enqueue are `consolidate` and `capture` (see §C.2 caret).

The DDL (`design.md` §2.3) has a `jobs` table but no semantics. Pin them:

```sql
-- extend jobs table
jobs(
  id TEXT PRIMARY KEY,           -- uuid
  kind TEXT NOT NULL,            -- 'extract' | 'consolidate' | 'reembed' | 'purge'
  payload TEXT NOT NULL,         -- json; for 'extract' = {raw_ref}, never the raw secret-bearing text inline if admission pending
  status TEXT NOT NULL,          -- 'pending' | 'leased' | 'done' | 'failed' | 'dead'
  attempts INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 5,
  run_after TEXT NOT NULL,       -- ISO8601; for backoff/debounce
  locked_at TEXT,                -- lease timestamp
  lease_owner TEXT,              -- worker uuid (process pid+boot id)
  dedup_key TEXT,                -- for debounce: e.g. 'consolidate:user=alice'  (UNIQUE partial idx on pending)
  last_error TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_jobs_dedup ON jobs(dedup_key) WHERE status IN ('pending','leased');
CREATE INDEX idx_jobs_ready ON jobs(status, run_after);
```

**Leasing (single-process in-proc worker, restart-safe):**
1. `claim()`: `UPDATE jobs SET status='leased', locked_at=now, lease_owner=me WHERE id = (SELECT id FROM jobs WHERE status='pending' AND run_after<=now ORDER BY run_after LIMIT 1) RETURNING *` — atomic via SQLite `BEGIN IMMEDIATE`. 
2. **Stale-lease reclaim** (crash recovery): on worker start AND periodically, `UPDATE jobs SET status='pending' WHERE status='leased' AND locked_at < now - LEASE_TTL` (LEASE_TTL=300s). A crashed worker's leased jobs return to the queue — no lost consolidation (the Letta/langmem fire-and-forget landmine, R5 anti-req).
3. **Idempotency**: every handler MUST be safe to run ≥1 time.
   - `extract`: keyed by source `content_hash`; re-running re-derives candidates → DEDUP collapses them. No double-insert because exact uuid5(normalized) dedup is the first gate.
   - `consolidate`: reads current state and is convergent (re-summarizing an already-summarized cluster is a no-op because the summary note already supersedes; guard: skip clusters whose members all have a `derived_from`-out edge).
   - `reembed`/`purge`: naturally idempotent (set/scrub).
4. **Backoff**: on failure `attempts++`, `run_after = now + min(2^attempts, 3600)s`. At `attempts>=max_attempts` → `status='dead'`, surfaced by `cold-frame doctor` (dead-letter count) — never silently dropped.

**Debounce**: producers set `dedup_key`; the unique partial index makes a second pending `consolidate:user=alice` a no-op insert (`INSERT ... ON CONFLICT(dedup_key) DO UPDATE SET run_after=min(run_after, excluded.run_after)` via the adapter's upsert method, not raw `INSERT OR REPLACE` — portability rule §1).

### C.2 Extraction LLM failure policy

> ^ SUPERSEDED — code wins (`cold_frame/api.py`, `store/base.py`). `Memory.add` extracts INLINE + synchronously; there is NO `extract` job. The only enqueued job kinds are `consolidate` (auto-maintenance) and `capture` (D26 auto-capture drain). The failure ladder below describes a queued-extract design that did not ship.

`add()` enqueues an `extract` job (the heavy LLM call is async per §4 PERSIST "async durable jobs 큐 경유"). Failure ladder:
1. LLM call raises (timeout/5xx/parse) → job retried with backoff (C.1).
2. Parse failure (LLM returned non-schema JSON): one in-call repair retry (re-prompt "return valid JSON for schema X"); if still bad, count as attempt and back off.
3. **Never drop the turn silently.** After `max_attempts`, job → `dead`; the raw turn is preserved as a `status='quarantine'` (see C.6) provenance-only note `{content: raw_turn, memory_type: episodic, confidence: 0.0, source: message}` so the user keeps the data and can re-extract. `doctor` reports dead extract jobs.
4. **Offline/`llm=None`**: no job — naive extraction inline (message = 1 fact), per SPEC §4. So `add` always succeeds synchronously even with zero LLM.

### C.3 Partial-write / single-transaction guarantee

SPEC §3 says `add_note` is a single transaction over notes+fts+vec+sources+history. Make it real:
- All five writes happen inside `BEGIN IMMEDIATE … COMMIT` in `SQLiteStore.add_note`. Any exception → `ROLLBACK` → caller sees the failure; **no half-written note, no SoT↔vec drift** (the dual-write drift anti-req).
- FTS5 is `content=''`-external-content over `notes`? No — use a standard FTS5 contentless-linked table updated in the same txn (explicit `INSERT INTO note_fts(rowid, content, keywords, tags)`), NOT triggers, so the write is one explicit transaction the Store controls (triggers + `content='notes'` make rebuilds/purges harder).
- vec: numpy default writes the embedding BLOB row in the same txn. `[vec]`/sqlite-vec path writes the vec0 row in the same txn. Either way, one commit.
- Conflict archive (old→archived + invalid_at + supersedes edge + history snapshot) is part of the **same** transaction as the new note insert — a crash cannot leave "new active but old also active".

### C.4 Corrupt / locked DB recovery

- **Locked** (`SQLITE_BUSY`): open with `busy_timeout=5000ms` + WAL mode. Writers serialize via `BEGIN IMMEDIATE`. The single in-proc worker + CLI/MCP may contend → on persistent busy, surface a clear "db busy, retry" rather than corrupting.
- **Corrupt** (`SQLITE_CORRUPT` / failed `PRAGMA integrity_check`): `cold-frame doctor` runs `PRAGMA integrity_check` and `PRAGMA quick_check`. On corruption: (1) refuse writes, (2) attempt `.recover` into a sidecar `memory.recovered.db`, (3) instruct restore-from-export (ties to the bundle spec). Never auto-overwrite the user's file.
- **WAL hygiene**: periodic `PRAGMA wal_checkpoint(TRUNCATE)` on idle; checkpoint before any export snapshot (resolves SPEC §3 "checkpointed read-only 스냅샷만").
- **Startup self-heal**: on open, run stale-lease reclaim (C.1) + `quick_check`; log result.

### C.5 Worker crash/restart

- Worker is a single in-proc thread/loop (`forget/worker.py`) with a `boot_id`. On start: reclaim stale leases (C.1.2), then poll loop `claim()→handle()→mark done/failed`.
- Poll = `run_after`-ordered; debounced via dedup_key. Empty queue → sleep with jittered backoff (e.g. 1–5s) to avoid busy-spin.
- Crash mid-job → lease expires → job reclaimed → idempotent re-run (C.1.3). No exactly-once needed because handlers are idempotent (at-least-once + idempotent = effectively-once).
- Graceful shutdown: stop claiming, let in-flight job finish or lease-expire.

### C.6 Quarantine status (resolves audit R6 / provenance invariant)

> ^ G2 RATIFIED (code wins): `Status` is EXACTLY 3 values (`active`/`archived`/`deleted`); quarantine is a flag column (`quarantined` bool), NOT a 4th `Status`. See `cold_frame/models.py`. The `Status = Literal[..., "quarantine"]` below is the pre-ratification shape.

`Status` is extended from the closed set to add `quarantine`:
```python
Status = Literal["active", "archived", "deleted", "quarantine"]
```
- A note enters `quarantine` when: provenance-less LLM assertion (no source row), confidence<0.4 durability-hold, or a dead extraction turn (C.2.3).
- **§5 FILTER default** = `status='active'` only → quarantined facts are **excluded from default search** but visible in Triage / `list --status quarantine`. (This was unspecified; pin it.)
- DB-level provenance invariant: a note with `status='active' AND confidence>=0.4` MUST have ≥1 `sources` row — enforced by a check in `add_note` (raise before commit) and an `integrity` eval case. (A pure SQL trigger is optional; the app-level guard is the source of truth.)

### C.7 Observability WITHOUT leaking secrets

Structured logging via `structlog`-style JSON to stderr (CLI) / file (server). **Hard rule: no note `content`, no source raw text, no secret/PII span is ever logged.** Loggable fields only:

```python
log.info("write.persist", note_id=id, memory_type=t, scope_hash=h(scope), 
         confidence=c, dedup="near_dup_merge", conflict="superseded", source_kind="message")
log.info("llm.call", task=task, model=llm.name, is_local=llm.is_local,
         prompt_tokens=u.p, completion_tokens=u.c, ms=elapsed)   # NEVER prompt/response text
log.info("job", kind=k, id=jid, status=s, attempts=n)            # NEVER payload
```
- A `redact_filter` runs on every log record: drops/masks keys in a denylist (`content`, `text`, `user`, `payload`, `raw`, `span`). Belt-and-suspenders against accidental future fields.
- `--debug` may add hashes/ids but still never raw content; a separate `--unsafe-trace` (off by default, warns loudly) is the ONLY way to see content, for local self-debugging.
- Eval `test_logs_have_no_content`: run a scenario with a sentinel string in note content; capture all log output; assert sentinel absent.

### C.8 Minimal metrics surface

`cold-frame doctor --json` / a `Memory.health()` returns counters (no external system needed; computed from SQLite):
```json
{
  "notes": {"active": N, "archived": N, "quarantine": N, "by_type": {...}},
  "jobs": {"pending": N, "leased": N, "dead": N, "oldest_pending_age_s": N},
  "conflicts_unresolved": N,        // held_for_human=1
  "db": {"size_bytes": N, "integrity": "ok", "wal_bytes": N},
  "embedder": {"name": "...", "dim": N, "stale_count": N},   // stale = embedded by a prior embedder
  "last_consolidate_at": "...",
  "search_p50_ms": N, "search_p95_ms": N    // rolling, in-memory ring buffer
}
```
`dead`-job count > 0 and `oldest_pending_age_s` large are the two alerts that matter.

---

## D. Performance Budget

### D.1 Targets (single-user local, M-class laptop, default HashEmbedder + numpy KNN)

| op | 1k facts | 10k facts | 100k facts |
|---|---|---|---|
| `add` (offline, no LLM job wait) | <15 ms | <20 ms | <30 ms |
| `add` (with async extract — returns after enqueue) | <15 ms | <20 ms | <30 ms |
| `search` (numpy KNN + FTS5 + RRF), k=8 | <25 ms | <60 ms | **<400 ms** ← KNN linear scan dominates |
| `search` with `[vec]` sqlite-vec | <20 ms | <35 ms | <70 ms |
| `consolidate` batch | background, not latency-critical | | |

> ^ These are **aspirational targets, NOT gated** — code wins. The `tests/perf/` perf-smoke test described below was never built; no perf budget is asserted in CI. Treat the numbers as design intent, not a live check.

These are p95 budgets, meant to be asserted loosely in a perf smoke test (`tests/perf/` marked `slow`, not in the merge gate; nightly). Generous (×2–3 headroom) so they're stable across machines.

### D.2 Where numpy KNN degrades → when to flip `[vec]`

- numpy brute-force KNN = O(N·dim) per query; fine to ~tens of thousands. With dim=256 (Hash) or 1536 (OpenAI), the matmul over a contiguous `float32` matrix is fast, but **the bottleneck is loading vectors from SQLite into the numpy matrix**. Mitigation: keep an in-memory `np.ndarray` cache of active embeddings, invalidated on write (single-user → cache is cheap and correct). With the cache, 100k×1536 cosine ≈ a 600 MB matmul → ~100–300 ms; without cache, the BLOB read dominates.
- **Flip rule (concrete):** default = numpy + in-memory cache. Auto-recommend `[vec]` when EITHER `active_vector_count > 50_000` OR measured `search_p95_ms > 250` (from C.8 ring buffer). `cold-frame doctor` prints: "벡터 N개, 검색 p95 X ms — `pip install cold-frame[vec]` 권장". Never auto-install; just advise. The Store interface (`knn`) is identical, so flipping is a config + extra, no app change.
- **Embedding-dimension consequence (cross-ref audit):** the in-memory matrix and any sqlite-vec table are sized by `Embedder.dim`. On embedder change, the cache and vec table are rebuilt by the `reembed` job; cross-embedder KNN is blocked (mixed-dim matmul is impossible) — search falls back to BM25-only with a `doctor` warning until reembed completes. This is the testable form of the deferred cross-tier consistency item, surfaced here because it is a *reliability* failure mode if unhandled (garbage KNN).

### D.3 FTS5 / RRF cost

FTS5 BM25 is index-backed → sub-ms to low-ms at 100k. RRF fuse is O(k·signals), trivial. So `search` cost is dominated by KNN, which is exactly what the `[vec]` flip addresses. No separate budget needed for FTS/RRF.

---

## E. Test inventory (the concrete files P1 starts)

```
cold_frame/eval/
  harness.py          # Suite/Case schema, ScriptedLLM, FrozenClock, run_suite, metrics
  datasets/
    extraction.yaml   freshness.yaml   dedup.yaml   forgetting.yaml
    precision_at_k.yaml   cross_scope.yaml   token_budget.yaml
    reliability.yaml  # purge-residue, no-secret-to-remote, no-content-in-logs, unbounded-growth, job-reclaim
tests/
  test_eval_suites.py   # parametrizes over every case → one pytest per case
  test_jobs_queue.py    # lease/reclaim/backoff/dedup/idempotency unit tests
  test_store_txn.py     # partial-write rollback, integrity_check, locked-db busy_timeout
# perf/test_perf_smoke.py — NOT BUILT (§D.1 caret): no perf-smoke test ships; D.1 budgets are un-gated targets.
```
