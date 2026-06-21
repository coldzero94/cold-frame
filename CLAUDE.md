# CLAUDE.md — Coldframe (`cold-frame`)

> Operating manual for coding this repo. **Rules, not prose. Follow exactly.**
> Coldframe = local-first, ownable LLM-agent memory layer. One SQLite file holds facts + BM25 + vectors + edges + versions + provenance. Works offline, no key, no server. The moat = **token-budget packer + forgetting/consolidation + deterministic conflict resolution**, built by hand and proven by deterministic mock-LLM tests (the discipline A-MEM/langmem skipped).

---

## 0. STOP — pre-P1 decision gates (불변 계약이 아직 갈라져 있음)

The five `docs/build/*.md` specs were written semi-independently and **contradict each other on exactly the seams TDD touches first.** Do NOT write P1 code until these are ratified into a single representation. Each, if guessed wrong, forces an engine-wide rewrite + golden-set rework. Status as of this writing: **NOT ready for clean P1.**

| Gate | Conflict | RATIFIED resolution (apply, then sweep all docs) |
|---|---|---|
| **G1 Sync core** | api-contract §0 = sync; read-and-budget §5.0 / eval §A = `async def` | **SYNC wins** (api-contract §0, D21·B6). `Memory`/`Store`/`Embedder`/`LLM` are all `def`. Edit read-and-budget §5.0 → `def search`; edit eval §A → sync `LLM.complete` + sync `ScriptedLLM`. |
| **G2 Quarantine model** | api-contract §1 = `pending` as 4th **Status** value; prompts §1.4 + read-and-budget §5.2 = Status stays 3-value, quarantine is a **`held_for_human`/`quarantined` flag column** + read-filter | **DECIDE BEFORE STEP 1.** Recommended = **flag column** (`held_for_human INTEGER`, `quarantined INTEGER`, `triage_reason TEXT`; `Status = active\|archived\|deleted`), because the provenance trigger + Triage queue already key on the flag. Then sweep SPEC §2/§5, data-layer §1/§1.1, api-contract §1, eval §C.6, read-and-budget §5.2, prompts §1.4 AND fix eval YAML `ExpectNote` fields to ONE representation. **The DDL, the `Status` Literal, and the read FILTER all depend on this.** |
| **G3 LLM/Embedder ABC** | `complete(...)` defined 3 ways (eval §A vs api-contract §6 vs prompts §8 `complete_json`); `Embedder.embed` returns `np.ndarray` (eval §A) vs `list[list[float]]` (api-contract §5) | Canonical ABC = **eval §A** shape (has `task: TaskTag` dispatch key + `Usage` + structured `schema`), reconciled to **sync** per G1. Pin in `cold_frame/llm/base.py`; make api-contract §6 + prompts `complete_json` point to it. Pick **`np.ndarray`** for `embed` (KNN matmul path). |
| **G4 Store ABC** | api-contract §3 and data-layer §9 list **divergent method sets** (`reinforce` vs `touch`, `complete_job/fail_job` vs `finish_job`, `purge_note` vs `purge`, `emb: list[float]` vs `emb: bytes`) | Produce ONE canonical `Store` ABC (merge both). Put it in `store/base.py`; data-layer §9 becomes a pointer. `emb` type must match the G3 Embedder return type. |
| **G5 Constants** | bands (3 vs 4), archive floor (0.20 vs 0.10), caps (`semantic=2000` vs `5000`, `episodic=500` vs `2000`) conflict numerically | Freeze ONE set in **`cold_frame/constants.py`**; all docs reference it. Ratified values in §3 below. **A forgetting test cannot be written against two cap tables.** |
| **G6 Clock/RNG seam** | eval §B.2 mandates injected `clock.now()` + seeded RNG + `uuid5` ids, but api-contract signatures don't carry `clock` consistently | Add `Clock` protocol + id-factory to `Memory.__init__` **before STEP 1** so the first commit bakes it in. Retrofitting time injection after write/read exist is the exact rework to avoid. |

**Infra gates (mechanical, low-risk — do at scaffold time):** `git init` + Python `.gitignore`; scaffold `cold_frame/` (SPEC §12) + `tests/`; pyproject `[tool.pytest.ini_options]` (markers `slow`,`live`) + `[tool.ruff]` + `[tool.mypy]`; expand `dev` extra (`pytest pytest-cov ruff mypy pyyaml hypothesis`) — **`pyyaml` is a required eval-harness dep currently unlisted**; add `.github/workflows/ci.yml` (ruff+mypy+`pytest -m "not slow and not live"`, no keys/network) + `.pre-commit-config.yaml`; add `cold_frame/branding.py`, `cold_frame/exceptions.py` (full hierarchy incl. `PolicyError`), `cold_frame/observability.py` (stdlib `logging` JSON formatter + `redact_filter`, NOT structlog in core).

---

## 1. Doc map — read before coding

**Code FROM these (authoritative, priority order):**
1. `docs/SPEC.md` — implementation spec. §1 packaging · §2 model · §3 storage · §4 write · §5 read · §6 forgetting · §7 procedural · §8 MCP · §9 UX/CLI · §10 eval · §11 build phases · §12 dirs · §15 reliability/concurrency.
2. `docs/build/api-contract.md` — **concrete signatures** + canonical strength/archive formulas + caps. **Pins interfaces; coding is mechanical from it** (once §0 gates are swept).
3. `docs/build/eval-and-reliability.md` — eval harness, golden-set format, `ScriptedLLM`, `FrozenClock`, `jobs` queue, failure ladders, perf budgets, test inventory.
4. `docs/build/{prompts,data-layer,read-and-budget}.md` — LLM prompts + JSON schemas; full DDL/event-log/migration; retrieve→RRF→rerank→budget + token counter.
5. `docs/security-spec.md` — purge invariant, localhost CSRF/DNS-rebind, MCP threat model, key lifecycle, import sandbox.

**Context only (the *why* — do not invent code from):** `docs/risks.md`, `docs/decisions.md` (D1–D21), `docs/requirements.md` (R1–R19 + anti-reqs), `docs/ux-design.md`, `docs/product-strategy.md`.

**Conflict-resolution rule:** `build/api-contract.md` + `build/eval-and-reliability.md` > `SPEC.md` > `decisions.md`/`ux-design.md`. **But where §0 lists an open gate, neither doc is yet authoritative — ratify the gate first, do not pick silently.** Already-decided: 3-band single strength formula (api-contract §4) supersedes ux §8.2/§4.3; the ux §4.3 `.42/.30/.18` weights are SUPERSEDED.

---

## 2. TDD workflow — MANDATORY, non-negotiable

**Every change is red → green → refactor. Test first, always.**

1. **RED** — write the failing test first. Engine behavior (extract/dedup/conflict/decay/budget/scope) → add/extend a **golden case** in `cold_frame/eval/datasets/*.yaml` (Suite/Case schema, eval §B). Plumbing (Store txn, jobs queue, exceptions) → unit test in `tests/`. Run it; confirm it fails for the right reason.
2. **GREEN** — minimum code to pass. No speculative scope.
3. **REFACTOR** — clean up green. Re-run.
4. **Full core suite before moving on** (`uv run pytest -m "not slow"`). Never leave a red suite. Never build the next unit on top of failures.

**The eval harness is the integration backbone.** Engine correctness is proven by deterministic mock-LLM golden cases, not hand-poking. `tests-core` (mock LLM + HashEmbedder, no network, no keys) is the merge gate (R16/R17). **Declare every LLM interaction in `llm_script`** — an unmatched call is a hard `EvalError` by design.

**Determinism is a CODE requirement (G6), not just a test trick:**
- Never call `datetime.utcnow()`/`datetime.now()`/`uuid4()` directly. Thread a `Clock` protocol + id-factory through `Memory`→`WriteCore`→`Store.reinforce/consolidate`/`forget`. Tests inject `FrozenClock` from `step.at` + `uuid5`.
- Seed all tiebreak RNG from `case.seed`.
- Eval-mode ids = `uuid5(NS, f"{case.id}:{ordinal}")` so assertions/snapshots are stable.
- HashEmbedder: seeded, dim=256, `blake2b(token)`→buckets, L2-normalized, no network — same embedder in prod default and tests (no embedder mock needed). `FixedVectorEmbedder` for cosine-band boundary cases.

---

## 3. INVARIANTS (철칙) — never violate without a new ADR

| # | Invariant | Meaning / enforcement |
|---|---|---|
| I1 | **Freshness = code, not LLM** | `valid_at` comparison decides supersession. LLM only proposes duplicate/contradiction (`ConflictVerdict`). Never let an LLM decide freshness/archive/merge-commit. Test: same case + garbage LLM hint → outcome unchanged. |
| I2 | **Archive, not delete** | Conflicts/forgetting set `status=archived` — row stays, revivable. **Only** secret/PII hard-purge deletes (D16, the documented exception). |
| I3 | **Single transaction** | `notes + note_fts + note_vec + sources + note_history + events` write in ONE txn (`BEGIN IMMEDIATE…COMMIT`). Conflict-archive of the old note is in the SAME txn as the new insert. Any exception → ROLLBACK. No half-write, no SoT↔vector/fts drift, no intermediate (new active AND old active) state observable. |
| I4 | **Sync core + one async seam** | All `Memory`/`Store`/`Embedder`/`LLM` methods are `def`. The ONLY `async def` is in `prompts/mcp.py`, wrapping each sync call in `anyio.to_thread.run_sync`. **No sync/async logic duplication.** Test: `inspect.iscoroutinefunction` false on every public method; `async def` greps only in `prompts/mcp.py`. |
| I5 | **Offline by default** | Default `Embedder=HashEmbedder`, `llm=None` → naive extract (1 message = 1 fact). `add`→`search` must work with zero keys, zero network. |
| I6 | **Admission before disk** | CLASSIFY→REDACT→CONFIDENCE-GATE→CONSENT runs before any write. Secrets BLOCKed and **never touch disk** (only a worthless tombstone). PII REDACTed. No path bypasses ADMISSION: `raw=True` and agent self-edit skip the extraction LLM but STILL run CLASSIFY→REDACT. `AddResult.blocked` carries reason/placeholder, NEVER secret content. |
| I7 | **Admission LLM strictly local (I-LOCAL)** | `task="admission_tiebreak"` (and any pre-persist secret-span eval) MUST use `is_local=True` LLM. `assert_local_for` raises `PolicyError` otherwise. Ambiguity fails **CLOSED** (BLOCK). A secret span NEVER reaches a remote endpoint. |
| I8 | **Portable schema** | Dialect-specific bits (FTS5/sqlite-vec/JSON) live behind the `Store` adapter. Timestamps = ISO8601-UTC TEXT. **No raw `INSERT OR REPLACE`**, no SQLite-only idioms in core. Vector dim read from `Embedder.meta`, never hardcoded (**no `FLOAT[1536]` literal**). |
| I9 | **Core deps = pydantic + numpy ONLY** | Core `cold_frame` imports nothing else. **`fastapi`/`psycopg` import in core = build break.** mcp SDK, openai, sqlite-vec, tiktoken, uvicorn, vue tooling — all behind extras (`[openai]`/`[local-llm]`/`[vec]`/`[ui]`/`[server]`), import-guarded. |
| I10 | **No SoT↔vector drift** | Vector + FTS + notes dual-written in the same txn (I3). `doctor` invariant: `count(notes)==count(fts)==count(vec)`. Canonical vector = BLOB; `[vec]` is an index on top. KNN hard-filters `embedder_id=current` (mixed-dim rows excluded → degrade to BM25-only). |
| I11 | **Stable ids, never display-index** | LLM sees stable note ids (UUID→int remap on extraction to prevent hallucination); never feed a display/list index as an id. |
| I12 | **No fire-and-forget** | Background work (consolidate/extract/reembed/purge) goes through the durable `jobs` queue: lease + stale-reclaim (after `LEASE_TTL`) + exponential backoff + dead-letter (`max_attempts`→`dead`, never silently dropped) + debounce (unique partial index on `dedup_key`). Handlers idempotent (at-least-once + idempotent = effectively-once). |
| I13 | **No infinite growth** | Forgetting/consolidation + per-scope caps (**`semantic=2000`, `episodic=500`, `procedural=100`**) keep active set bounded. `access_log` capped at 50 rows/note. consolidate is non-destructive + convergent (re-run = no-op); pinned/high-importance never archived. |
| I14 | **Provenance invariant** | `status='active' AND confidence>=0.4` ⇒ ≥1 `sources` row (enforced in `add_note` pre-commit guard AND DB trigger). Provenance-less or `confidence<0.4` ⇒ quarantine (per G2 representation), **excluded from default search**, visible only via Triage / `held_for_triage`/`by_status`. |
| I15 | **One WriteCore** | `add()`, `correct_memory()`, and all self-edit tools (`create_fact`/`update_fact`/`supersede`) converge on `WriteCore.commit`/`commit_supersede`: same ADMISSION→DEDUP→CONFLICT→PERSIST. Exactly ONE persist path (D8). `correct_memory` routes through `commit_supersede` (NOT similarity search) + runs ADMISSION on new text. |
| I16 | **Observability never leaks** | Structured JSON to stderr. **Never log note content / source raw text / secrets / payload.** `redact_filter` masks denylist (`content,text,user,payload,raw,span`); only ids/hashes/tasks/counters/`is_local`/token-counts logged. `--unsafe-trace` is the only content path, off by default. Test: `test_logs_have_no_content` (sentinel string absent from all output). |
| I17 | **No live `.db` sync** | export/backup = checkpointed read-only snapshot or event-log dump (`events.ndjson`). Never sync the live WAL file (corruption). Import is idempotent, keyed on `event_id`. |

**Frozen constants (`cold_frame/constants.py`, ratified — G5):**
`S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))` ·
bands: evergreen 🌳 `S≥0.66` / budding 🌿 `0.33≤S<0.66` / fading 🌱 `S<0.33` (3 bands; `0.10` is a fading sub-label, not a 4th band) ·
`at_risk` ○ overlay (band-independent): `confidence<0.4 OR (now−last_accessed)>60d` ·
archive fires ONLY when `S<0.33 AND archive_score<ARCHIVE_THRESHOLD=0.20`, OR on capacity cap ·
caps: `semantic=2000, episodic=500, procedural=100` (per scope) ·
`REINFORCE_DECAY_INC=0.5`, `DECAY_S_CAP=365.0`, RRF `k_const=60` (no global divisor), `FANOUT=4` (min 20, max 200), cosine dedup bands `0.82`/`0.93`, importance EMA `α=0.1`, archive_score weights `0.5/0.3/0.2`, HashEmbedder `dim=256`.

---

## 4. Conventions

- **Python 3.11+**, package `cold_frame`, dist `cold-frame`, managed by **uv**.
- **Pydantic v2** for all models. Timestamps = tz-aware UTC `datetime` in Python; Store serializes to ISO8601-UTC TEXT.
- **Sync core + thin `to_thread` async facade** (I4). One DB connection per thread + small pool; **LLM I/O is called OUTSIDE write txns — never hold a lock across an LLM call.**
- **ABC seams (sacred, D10):** `Store` (`store/base.py`), `Embedder`+`LLM` (`llm/base.py`). SQLiteStore is v1; PostgresStore later behind the identical contract. Every LLM call passes a `TaskTag` enum (mock dispatch + local-only enforcement + log key).
- **Deterministic + time-injected:** `Clock` protocol, seeded RNG, `uuid5` eval ids (§2, G6).
- **Branding indirection:** route every literal `cold-frame`/path/port through `cold_frame/branding.py` (`PKG`, `DB_DIR`, `MCP_ID`, `URL_SCHEME`, `UI_PORT=27182`) — rename is one file. Forbid literal name strings elsewhere (grep check).
- **Exceptions:** one hierarchy in `cold_frame/exceptions.py` (`ColdFrameError` → `NoteNotFound`, `EmbedderMismatchError`, `SecretBlocked`, `VarHealerError`, `StoreError`, `PolicyError`). The MCP error→code map (`invalid_scope`/`not_found`/`internal`) is 1:1 with these classes, pinned in one place.
- **Directory layout (SPEC §12):** `models.py api.py` + `store/ write/ read/ forget/ procedural/ llm/ prompts/ ui/ eval/`. **CLI lives at `prompts/cli.py`** — NOTE: `pyproject.toml` currently points `cold-frame = "cold_frame.cli:main"`; reconcile the entrypoint to the actual module before P1 ships.
- **Prose Korean OK, code/schemas/identifiers English.**

---

## 5. ANTI-PATTERNS — do NOT do these

- **No default graph DB** (Neo4j etc.). Edges are lightweight SQL rows; SQL until genuine multi-hop need.
- **No LLM-delegated freshness/archive/merge-commit** (I1).
- **No secret span to a remote LLM** (I7) — admission tie-break is local-only, fail-closed.
- **No infinite accumulation** — forgetting + caps not optional (I13).
- **No fire-and-forget consolidation** — durable queue only (I12).
- **No SoT↔vector drift** — same-txn dual-write only (I3/I10). Do NOT update vec/fts in a separate step or via triggers; the Store writes them explicitly in one txn.
- **No display-index-as-id to the LLM** (I11).
- **No sync/async logic duplication** — one sync impl; MCP is the only async wrapper (I4).
- **No `fastapi`/`psycopg`/heavy deps in core** (I9). New runtime dep ⇒ extras + ADR.
- **No global graph / hairball UI** — graph is a local 1–2 hop ego lens; the hero is *state* (decay/belief), not topology.
- **No global-divisor RRF footgun** — RRF with `k_const=60` (SPEC §5).
- **No hardcoded vector dim** — from `Embedder.meta.dim`, written at migrate time (I8).
- **No reposition on UI transitions** — opacity/size only (spatial memory). Toasts only when belief *changes*.
- **No OAuth/server for local integration** — local MCP is stdio, OAuth-free (D11).

---

## 6. Build order (P1→P6) — acceptance = test gate

Implement strictly in order. **A phase is "done" only when its mapped eval suites are green** (eval §B.4).

| Phase | Deliverable | Acceptance / gate suites |
|---|---|---|
| **P1 skeleton** | Store (single `.db`) + models + `add`(extract) + `search`(hybrid+RRF) + CLI + minimal MCP server + eval harness | offline `add`→`search` recalls the just-added fact; `claude mcp add` tool callable. **Gate: extraction + precision_at_k + cross_scope green.** |
| **P2 correctness** | tiered dedup + bi-temporal conflict + deterministic freshness + provenance/versions | **Gate: dedup + freshness green.** |
| **P3 read quality + UI** | token-budget packer + optional rerank + meta boost + local web UI | **Gate: token_budget green;** `cold-frame ui` visualizes. |
| **P4 forgetting** | decay + consolidation + durable worker + capacity cap | **Gate: forgetting green + `test_no_unbounded_growth`.** |
| **P5 procedural** | gradient optimize + var-healer | prompt self-improves; f-string vars preserved (`VarHealerError` on drop); `warrants_adjustment=False` ⇒ no edit. |
| **P6 agentic write** | self-edit tools (common WriteCore) | **Gate: run dedup+freshness suites THROUGH the self-edit tool path** (op routed via `memory_tools`) — proves single WriteCore (I15). |

**Always-on reliability gates (every PR):** `test_purge_leaves_no_residue`, `test_no_secret_to_remote`, `test_admission_tiebreak_rejects_remote_llm`, `test_logs_have_no_content`, jobs lease/reclaim/backoff/idempotency, Store partial-write rollback + `PRAGMA integrity_check`.

---

## 7. Commands

```bash
# env / deps
uv sync                                # core + dev deps
uv sync --extra openai --extra vec     # with extras when needed
uv add <pkg>                           # add a dep — ONLY per §8 policy

# tests (TDD loop)
uv run pytest -m "not slow"                            # CORE gate: mock-LLM + HashEmbedder, no network. Run before moving on.
uv run pytest cold_frame/eval -k freshness             # one suite
uv run pytest -m slow                                  # perf smoke (nightly, not merge gate)

# quality
uv run ruff check . && uv run ruff format .
uv run mypy cold_frame

# smoke the product (offline)
uv run cold-frame add "I prefer dark roast" && uv run cold-frame search "coffee"
uv run cold-frame doctor               # install/DB/embedder/MCP + invariant checks
```

Run `ruff` + `mypy` + core `pytest` clean before considering any unit of work complete.

---

## 8. Guardrails

- **Small commits.** One red→green→refactor cycle per commit; tests in the same commit.
- **Dependency policy (hard):** core stays `pydantic + numpy`. Anything else MUST land behind an extra (`[openai]`/`[local-llm]`/`[vec]`/`[ui]`/`[server]`), import-guarded so core never imports it. Adding a core dep requires an ADR in `decisions.md`. **`fastapi`/`psycopg` in core = build break.** (`pyyaml` is a *dev/eval* dep — add to the `dev` extra, not core.)
- **Doctor is an invariant check, not just info.** Verifies `notes==fts==vec`, runs `PRAGMA integrity_check`/`quick_check`, reports dead jobs, oldest-pending age, stale vectors, embedder dim. **If you add a grain to the write txn, add it to the doctor invariant.**
- **Schema changes are additive + idempotent**, gated by `user_version`. Non-additive migrations auto-snapshot first. Never drop/rewrite v1 columns. "No migration" in SPEC = no manual user step, NOT no schema versioning. **Migrations must not break the offline default** (fresh install, no key, must `add`→`search`).
- **Never weaken an invariant to make a test pass.** If a test forces an invariant change, that needs an ADR, not a quiet edit. Failing CLOSED (BLOCK on ambiguity) is always the safe admission default.
- **Publish gate:** D19 (`name=cold-frame`) vs D-P2 (abandon the name) is unresolved + PyPI/trademark unverified. **No `twine upload` step until that closes.**
---

## 9. 코드 스타일 & 로컬 포트 (2026-06-21)

- **Style = PEP8 + 전체 type hints, 도구가 강제.** `ruff` (E/W=PEP8 · I · UP · B · SIM · PTH · **ANN**=annotations) + `ruff format` + `mypy --strict`. 설정은 `pyproject.toml` `[tool.ruff]`/`[tool.mypy]`/`[tool.pytest.ini_options]`. **모든 공개 함수/메서드에 타입힌트** (mypy strict가 미주석 def 거부). 커밋 전 자동: `.pre-commit-config.yaml` (`uv run pre-commit install`).
- **DB는 포트가 없다.** SQLite 단일 파일(`~/.cold-frame/memory.db`) → 포트 충돌 자체가 0 (로컬 설치 마찰 없음). 포트를 쓰는 건 *로컬 웹 UI 서버* 하나뿐.
- **로컬 UI 포트 = 마찰 0 전략.** 기본 `branding.UI_PORT=27182` (일부러 흔하지 않은 값, 일반 dev 포트 회피). **점유 시 자동으로 다음 빈 포트로 fallback** + `127.0.0.1` 전용 bind. 해결된 포트를 `~/.cold-frame/ui.port`에 기록 → CLI/MCP deep-link가 *해결된* 포트를 읽어 stale 안 됨. `cold-frame ui --port N` override, `doctor`가 실제 포트 보고. literal 포트 금지(`branding.UI_PORT`만, I-branding).
- **G2 비준 (quarantine 표현) = flag 컬럼.** `Status = active|archived|deleted` (3-value 유지) + `held_for_human`/`quarantined`/`triage_reason` 컬럼. 기본 검색 = `status='active' AND NOT quarantined`. 이전 D21-B4의 'pending status'는 **superseded**; SPEC/data-layer/api-contract/eval/read-and-budget 5문서 sweep은 #1 잔여 작업에서.
