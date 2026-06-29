# CLAUDE.md — Coldframe (`cold-frame`)

> Operating manual for coding this repo. **Rules, not prose. Follow exactly.**
> Coldframe = a local-first, ownable LLM-agent memory layer. One SQLite file holds facts + BM25 + vectors + edges + versions + provenance. Works offline, no key, no server. The moat = **token-budget packer + forgetting/consolidation + deterministic conflict resolution**, built by hand and proven by deterministic mock-LLM tests (the discipline A-MEM/langmem skipped).

---

## Status — where this repo is

**P1–P6 are built** (TDD, §2): the offline `add`→`search`→conflict/freshness→budget→forgetting/consolidation→procedural→self-edit engine works, with CLI + MCP stdio server + Claude Code plugin + local web UI. ~350 deterministic tests green on a **3.12/3.13 CI matrix** (`ruff` + `mypy --strict` + pytest + a built-wheel install smoke). The auto-memory loop (recall + capture) is **live-verified against real Claude Code** (headless `claude -p`). A multi-agent hardening audit fixed 17 findings (timestamp-width/`as_of`, jobs dedup-collision recovery, consolidation atomicity, compaction-rescan resurrection, …).

**NOT done — genuine, known gaps (don't claim otherwise):** privacy/security per **D25** is PARTIAL — obvious secrets BLOCKed pre-disk; **PII redaction (email/phone/card/ssn) built but OPT-IN** (`Memory(pii_redact=…)` / `add --redact-pii`); **at-rest encryption built but OPT-IN** (the `[crypto]` extra = SQLCipher; `Memory(encryption_key=…)` / `$COLD_FRAME_KEY`; whole DB + WAL + snapshots, set at creation; adversarially reviewed, CI-verified on Linux). Still **NOT built**: the admission LLM tiebreak (I7), confidence-gate/consent, crypto-shred purge, and a plaintext→encrypted migration for an existing DB (deferred to v1.1/hosted). `PolicyError` + `llm.assert_local_for` are tested **scaffolding with no live caller in v1** (re-activate when the I7 tiebreak lands). A PyPI release is pending (D19 name locked; trusted-publisher + tag are the user's call). Never silently diverge from a contract — change it with an ADR in `docs/decisions.md`.

---

## 1. Doc map — read before coding

**Code FROM these (authoritative, priority order):**
1. `docs/SPEC.md` — implementation spec. §1 packaging · §2 model · §3 storage · §4 write · §5 read · §6 forgetting · §7 procedural · §8 MCP · §9 UX/CLI · §10 eval · §11 build phases · §12 dirs · §15 reliability/concurrency.
2. `docs/build/api-contract.md` — concrete signatures + canonical strength/archive formulas + caps. Pins interfaces; coding is mechanical from it.
3. `docs/build/eval-and-reliability.md` — eval harness, golden-set format, `ScriptedLLM`, `FrozenClock`, `jobs` queue, failure ladders, perf budgets, test inventory.
4. `docs/build/{prompts,data-layer,read-and-budget}.md` — LLM prompts + JSON schemas; full DDL/event-log/migration; retrieve→RRF→rerank→budget + token counter.
5. `docs/security-spec.md` — purge invariant, localhost CSRF/DNS-rebind, MCP threat model, key lifecycle, import sandbox.

**Context only (the *why* — do not invent code from):** `docs/risks.md`, `docs/decisions.md` (D1–D22), `docs/requirements.md` (R1–R19 + anti-reqs), `docs/ux-design.md`, `docs/product-strategy.md`, `docs/tdd-plan.md`.

**Conflict-resolution rule:** `build/api-contract.md` + `build/eval-and-reliability.md` > `SPEC.md` > `decisions.md`/`ux-design.md`. The G2/G3/G4/G5 seams are now pinned in code (`cold_frame/models.py`, `constants.py`, `llm/base.py`, `store/base.py`) — **code wins for those.** If a doc still shows an old shape (e.g. `pending` status, `list[float]` embeddings), the code is right. The ux §8.2/§4.3 strength weights are superseded by the single api-contract/`constants.py` formula.

---

## 2. TDD workflow — MANDATORY, non-negotiable

**Every change is red → green → refactor. Test first, always.**

1. **RED** — write the failing test first. Engine behavior (extract/dedup/conflict/decay/budget/scope) → add/extend a **golden case** in `cold_frame/eval/datasets/*.yaml` (Suite/Case schema, eval §B). Plumbing (Store txn, jobs queue, exceptions) → unit test in `tests/`. Run it; confirm it fails for the right reason.
2. **GREEN** — minimum code to pass. No speculative scope.
3. **REFACTOR** — clean up green. Re-run.
4. **Full core suite before moving on** (`uv run pytest -m "not slow"`). Never leave a red suite. Never build the next unit on top of failures. (A blocking Stop hook in `.claude/` enforces this.)

**The eval harness is the integration backbone.** Engine correctness is proven by deterministic mock-LLM golden cases, not hand-poking. `tests-core` (mock LLM + HashEmbedder, no network, no keys) is the merge gate (R16/R17). **Declare every LLM interaction in `llm_script`** — an unmatched call is a hard `EvalError` by design.

**Determinism is a CODE requirement, not just a test trick:**
- Never call `datetime.utcnow()`/`datetime.now()`/`uuid4()` directly. Thread a `Clock` protocol + id-factory through `Memory`→`WriteCore`→`Store.reinforce/consolidate/forget`. Tests inject `FrozenClock` + `uuid5`.
- Seed all tiebreak RNG from `case.seed`.
- Eval-mode ids = `uuid5(NS, f"{case.id}:{ordinal}")` so assertions/snapshots are stable.
- HashEmbedder: seeded, dim=256, `blake2b(token)`→buckets, L2-normalized, no network — the same embedder in the prod default and tests (no embedder mock needed). `FixedVectorEmbedder` for cosine-band boundary cases.

---

## 3. INVARIANTS — never violate without a new ADR

| # | Invariant | Meaning / enforcement |
|---|---|---|
| I1 | **Freshness = code, not LLM** | `valid_at` comparison decides supersession. The LLM only proposes duplicate/contradiction (`ConflictVerdict`). Never let an LLM decide freshness/archive/merge-commit. Test: same case + garbage LLM hint → outcome unchanged. |
| I2 | **Archive, not delete** | Conflicts/forgetting set `status=archived` — the row stays, revivable. **Only** secret/PII hard-purge deletes (D16, the documented exception). |
| I3 | **Single transaction** | `notes + note_fts + note_vec + sources + note_history + events` write in ONE txn (`BEGIN IMMEDIATE…COMMIT`). The conflict-archive of the old note is in the SAME txn as the new insert. Any exception → ROLLBACK. No half-write, no SoT↔vector/fts drift, no observable intermediate (new active AND old active) state. |
| I4 | **Sync core + one async seam** | All `Memory`/`Store`/`Embedder`/`LLM` methods are `def`. The ONLY `async def` is in `cold_frame/mcp.py`, wrapping each sync call in `anyio.to_thread.run_sync`. **No sync/async logic duplication.** Test: `inspect.iscoroutinefunction` false on every public method; `async def` greps only in `cold_frame/mcp.py`. |
| I5 | **Offline by default** | Default `Embedder=HashEmbedder`, `llm=None` → naive extract (1 message = 1 fact). `add`→`search` must work with zero keys, zero network. |
| I6 | **Admission before disk** ⚠️ **PARTIAL in v1 (D25)** — DONE: a deterministic secret-BLOCK (`write/admission.py` regex+entropy) runs in `WriteCore.commit`/`commit_supersede` BEFORE embed/persist/host-sampling; an obvious secret is BLOCKed (never on disk, never to the host), reported in `AddResult.blocked` (content-free placeholder) or raised as `SecretBlocked` on the supersede path. PII REDACT (`write/admission.py:redact_pii`, email/phone/card/ssn) is built but **OPT-IN** (`Memory(pii_redact=…)` / `add --redact-pii`; off by default — a personal store keeps your own contact facts), reported content-free in `AddResult.redacted`. DEFERRED (v1.1/hosted): CONFIDENCE-GATE/CONSENT, the I7 local tiebreak, crypto-shred purge. |
| I7 | **Admission LLM strictly local** ⚠️ **DEFERRED in v1 (D25)** — plumbing exists (`assert_local_for`, `SamplingLLM.is_local=False`) but no admission call is dispatched. Design: `task="admission_tiebreak"` (+ pre-persist secret-span eval) MUST use `is_local=True`; ambiguity fails **CLOSED** (BLOCK); a secret span NEVER reaches a remote endpoint. Re-applies when I6 lands. |
| I8 | **Portable schema** | Dialect-specific bits (FTS5/sqlite-vec/JSON) live behind the `Store` adapter. Timestamps = ISO8601-UTC TEXT. **No raw `INSERT OR REPLACE`**, no SQLite-only idioms in core. Vector dim is read from `Embedder.meta`, never hardcoded (**no `FLOAT[1536]` literal**). |
| I9 | **Core deps = pydantic + numpy ONLY** | Core `cold_frame` imports nothing else. **`fastapi`/`psycopg` import in core = build break.** mcp SDK, openai, sqlite-vec, tiktoken, uvicorn, vue tooling — all behind extras (`[openai]`/`[local-llm]`/`[vec]`/`[ui]`/`[server]`), import-guarded. |
| I10 | **No SoT↔vector drift** | Vector + FTS + notes are dual-written in the same txn (I3). `doctor` invariant: `count(notes)==count(fts)==count(vec)`. Canonical vector = BLOB; `[vec]` is an index on top. KNN hard-filters `embedder_id=current` (mixed-dim rows excluded → degrade to BM25-only). |
| I11 | **Stable ids, never display-index** | The LLM sees stable note ids (UUID→int remap on extraction to prevent hallucination); never feed a display/list index as an id. |
| I12 | **No fire-and-forget** | Background work (consolidate/extract/reembed/purge) goes through the durable `jobs` queue: lease + stale-reclaim (after `LEASE_TTL`) + exponential backoff + dead-letter (`max_attempts`→`dead`, never silently dropped) + debounce (unique partial index on `dedup_key`). Handlers are idempotent (at-least-once + idempotent = effectively-once). |
| I13 | **No infinite growth** | Forgetting/consolidation + per-scope caps (`semantic=2000`, `episodic=500`, `procedural=100`) keep the active set bounded. `access_log` is capped at 50 rows/note. consolidate is non-destructive + convergent (re-run = no-op); pinned/high-importance is never archived. |
| I14 | **Provenance invariant** | `status='active' AND confidence>=0.4` ⇒ ≥1 `sources` row (enforced in the `add_note` pre-commit guard AND a DB trigger). Provenance-less or `confidence<0.4` ⇒ quarantine (`quarantined=True`), **excluded from default search**, visible only via Triage / `by_status`. |
| I15 | **One WriteCore** | `add()`, `correct_memory()`, and all self-edit tools (`create_fact`/`update_fact`/`supersede`) converge on `WriteCore.commit`/`commit_supersede`: the same ADMISSION→DEDUP→CONFLICT→PERSIST. Exactly ONE persist path (D8). `correct_memory` routes through `commit_supersede` (NOT similarity search) and runs ADMISSION on the new text. |
| I16 | **Observability never leaks** | Structured JSON to stderr. **Never log note content / source raw text / secrets / payload.** `redact_filter` masks the denylist (`content,text,user,payload,raw,span`); only ids/hashes/tasks/counters/`is_local`/token-counts are logged. `--unsafe-trace` is the only content path, off by default. Test: `test_logs_have_no_content`. |
| I17 | **No live `.db` sync** | export/backup = a checkpointed read-only snapshot or event-log dump (`events.ndjson`). Never sync the live WAL file (corruption). Import is idempotent, keyed on `event_id`. |

**Frozen constants** (`cold_frame/constants.py`, the single source of truth):
`S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))` ·
bands: evergreen `S≥0.66` / budding `0.33≤S<0.66` / fading `S<0.33` (3 bands; `0.10` is a fading sub-label, not a 4th band) ·
`at_risk` overlay (band-independent): `confidence<0.4 OR (now−last_accessed)>60d` ·
archive fires ONLY when `S<0.33 AND archive_score<ARCHIVE_THRESHOLD=0.20`, OR on a capacity cap ·
caps: `semantic=2000, episodic=500, procedural=100` (per scope) ·
`REINFORCE_DECAY_INC=0.5`, `DECAY_S_CAP=365.0`, RRF `k_const=60` (no global divisor), `FANOUT=4` (min 20, max 200), cosine dedup bands `0.82`/`0.93`, importance EMA `α=0.1`, archive_score weights `0.5/0.3/0.2`, HashEmbedder `dim=256`.

---

## 4. Conventions

- **Python 3.12+**, package `cold_frame`, dist `cold-frame`, managed by **uv**.
- **Code style is tool-enforced (PEP8 + full type hints).** `ruff` (E/W=PEP8 · I · UP · B · SIM · PTH · **ANN**=annotations) + `ruff format` + `mypy --strict`; config in `pyproject.toml`. **Every public function/method is type-hinted** (mypy strict rejects un-annotated `def`). `.pre-commit-config.yaml` runs ruff + mypy before each commit (`uv run pre-commit install`). Do NOT weaken rules to pass — fix the code/types.
- **Language:** all code, comments, docstrings, identifiers, and commit messages in **English**. (The planning docs under `docs/` are Korean — leave them as-is.)
- **Pydantic v2** for all models. Timestamps = tz-aware UTC `datetime` in Python; the Store serializes to ISO8601-UTC TEXT.
- **Sync core + thin `to_thread` async facade** (I4). One DB connection per thread + a small pool; **LLM I/O is called OUTSIDE write txns — never hold a lock across an LLM call.**
- **ABC seams (sacred, D10):** `Store` (`store/base.py`), `Embedder` + `LLM` (`llm/base.py`). `SQLiteStore` is v1; `PostgresStore` later behind the identical contract. Every LLM call passes a `TaskTag` enum (mock dispatch + local-only enforcement + log key).
- **Deterministic + time-injected:** `Clock` protocol, seeded RNG, `uuid5` eval ids (§2).
- **Branding indirection:** route every literal name/path/port through `cold_frame/branding.py` (`PKG`, `DB_DIR`, `MCP_ID`, `URL_SCHEME`, `UI_PORT=27182`) — a rename is one file. No literal name/port strings elsewhere (grep check).
- **Ports / local server:** the **DB has no port** — it is a SQLite file (`~/.cold-frame/memory.db`), so zero port collision. Only the local web UI server uses a port: default `branding.UI_PORT=27182` (deliberately uncommon), **auto-fallback to the next free port if occupied**, `127.0.0.1`-only bind, the resolved port written to `~/.cold-frame/ui.port` so CLI/MCP deep-links never go stale; `cold-frame ui --port N` overrides; `doctor` reports it.
- **Exceptions:** one hierarchy in `cold_frame/exceptions.py` (`ColdFrameError` → `NoteNotFound`, `EmbedderMismatchError`, `SecretBlocked`, `VarHealerError`, `StoreError`, `PolicyError`). The MCP error→code map (`invalid_scope`/`not_found`/`internal`) is 1:1 with these classes, pinned in `mcp_code_for`.
- **Directory layout (SPEC §12):** `models.py api.py cli.py mcp.py` + `store/ write/ read/ forget/ procedural/ llm/ prompts/ ui/ eval/`. CLI entrypoint = `cold_frame.cli:main` (matches `pyproject.toml`); the MCP stdio server = `cold_frame/mcp.py`.

---

## 5. ANTI-PATTERNS — do NOT do these

- **No default graph DB** (Neo4j etc.). Edges are lightweight SQL rows; SQL until a genuine multi-hop need.
- **No LLM-delegated freshness/archive/merge-commit** (I1).
- **No secret span to a remote LLM** (I7) — the admission tie-break is local-only, fail-closed.
- **No infinite accumulation** — forgetting + caps are not optional (I13).
- **No fire-and-forget consolidation** — durable queue only (I12).
- **No SoT↔vector drift** — same-txn dual-write only (I3/I10). Do NOT update vec/fts in a separate step or via triggers; the Store writes them explicitly in one txn.
- **No display-index-as-id to the LLM** (I11).
- **No sync/async logic duplication** — one sync impl; MCP is the only async wrapper (I4).
- **No `fastapi`/`psycopg`/heavy deps in core** (I9). A new runtime dep ⇒ an extra + an ADR.
- **No global graph / hairball UI** — the graph is a local 1–2 hop ego lens; the hero is *state* (decay/belief), not topology.
- **No global-divisor RRF footgun** — RRF with `k_const=60` (SPEC §5).
- **No hardcoded vector dim** — from `Embedder.meta.dim`, written at migrate time (I8).
- **No reposition on UI transitions** — opacity/size only (spatial memory). Toasts only when a belief *changes*.
- **No OAuth/server for local integration** — local MCP is stdio, OAuth-free (D11).
- **No hand-maintained / hand-edited API types** (D20). The JSON wire contract is single-sourced in `cold_frame/ui/contract.py` (TypedDicts); `frontend/src/api.schema.json` + `api.generated.ts` are GENERATED — never edit them or re-declare the shapes in TS. After a contract change run `pnpm -C frontend run gen:types` and commit both artifacts. The drift proof-chain is mandatory and must not be weakened: mypy (builder static type) · `test_ui_contract_roundtrip.py` (runtime JSON validates against the contract, strict) · `test_api_contract.py` (schema == contract; every Literal hoisted) · CI **`codegen-drift`** git-diff (authoritative TS guard — `vue-tsc` is consumption-gated, do NOT drop the job assuming it covers unconsumed fields).

---

## 6. Build order (P1→P6) — acceptance = test gate

Implement strictly in order. **A phase is "done" only when its mapped eval suites are green** (eval §B.4).

| Phase | Deliverable | Acceptance / gate suites |
|---|---|---|
| **P1 skeleton** | Store (single `.db`) + models + `add`(extract) + `search`(hybrid+RRF) + CLI + minimal MCP server + eval harness | offline `add`→`search` recalls the just-added fact; `claude mcp add` tool callable. **Gate: extraction + precision_at_k + cross_scope green.** |
| **P2 correctness** | tiered dedup + bi-temporal conflict + deterministic freshness + provenance/versions | **Gate: dedup + freshness green.** |
| **P3 read quality + UI** | token-budget packer + optional rerank + meta boost + local web UI | **Gate: token_budget green;** `cold-frame ui` visualizes. |
| **P4 forgetting** | decay + consolidation + durable worker + capacity cap | **Gate: forgetting green + `test_no_unbounded_growth`.** |
| **P5 procedural** | gradient optimize + var-healer | the prompt self-improves; f-string vars preserved (`VarHealerError` on drop); `warrants_adjustment=False` ⇒ no edit. |
| **P6 agentic write** | self-edit tools (common WriteCore) | **Gate: run dedup+freshness suites THROUGH the self-edit tool path** (op routed via `memory_tools`) — proves the single WriteCore (I15). |

**Always-on reliability gates (every PR):** jobs lease/reclaim/backoff/idempotency, Store partial-write rollback + `PRAGMA integrity_check`. *(`test_purge_leaves_no_residue` is **DONE** — `Store.purge` scrubs every grain (incl. event-log payload, the I2/I17 carve-out) + VACUUM + grep-verifies the plaintext is gone from the live `.db`/`.db-wal` (honest scope: live files only; the SQLCipher crypto-shred variant stays deferred). The admission/remote gates `test_no_secret_to_remote`, `test_admission_tiebreak_rejects_remote_llm` are **DEFERRED with I6/I7 per D25** — they apply when admission lands in v1.1/hosted. `test_logs_have_no_content` is landed: the redact filter is the one built control.)*

---

## 7. Commands

```bash
# env / deps
uv sync                                # core + dev deps
uv sync --extra openai --extra vec     # with extras when needed
uv add <pkg>                           # add a dep — ONLY per §8 policy

# tests (TDD loop)
uv run pytest -m "not slow"            # CORE gate: mock-LLM + HashEmbedder, no network. Run before moving on.
uv run pytest cold_frame/eval -k freshness   # one suite
uv run pytest -m slow                  # perf smoke (nightly, not the merge gate)

# quality
uv run ruff check . && uv run ruff format .
uv run mypy cold_frame

# smoke the product (offline)
uv run cold-frame add "I prefer dark roast" && uv run cold-frame search "coffee"
uv run cold-frame doctor               # install/DB/embedder/MCP + invariant checks

# web UI (Vue SPA — source in frontend/, D20). Node/pnpm are MAINTAINER-only release tools,
# never a pip/uv dep: the built bundle ships in the wheel (cold_frame/ui/_dist via hatch
# artifacts) so users need no Node. Server (stdlib) degrades to an inline inspector if unbuilt.
pnpm -C frontend install                # once
pnpm -C frontend run build              # → cold_frame/ui/_dist (then served by `cold-frame ui`)
pnpm -C frontend run typecheck          # vue-tsc; uv run python scripts/check_ui_bundle.py before release
pnpm -C frontend run gen:types          # regen API types from cold_frame/ui/contract.py (run after a shape change)
```

The JSON API contract is single-sourced in `cold_frame/ui/contract.py` (TypedDicts). `gen:types`
emits `frontend/src/api.schema.json` (pydantic) → `api.generated.ts` (json-schema-to-typescript);
both are committed. Edit `contract.py`, run `gen:types`, commit. The full proof chain that Python and
TS can't drift: mypy (builder static type == TypedDict) · `test_ui_contract_roundtrip.py` (the real
runtime JSON validates against the contract) · `test_api_contract.py` (committed schema == contract) ·
the CI **`codegen-drift`** job (committed `api.generated.ts` == regenerated). NOTE: `codegen-drift`
(git-diff), not `vue-tsc`, is the authoritative TS-drift guard — `vue-tsc` only catches a field a
`.vue`/`.ts` file actually reads, so don't drop the CI job assuming typecheck covers it.

Run `ruff` + `mypy` + the core `pytest` clean before considering any unit of work complete.

---

## 8. Guardrails

- **Small commits.** One red→green→refactor cycle per commit; tests in the same commit.
- **Dependency policy (hard):** core stays `pydantic + numpy`. Anything else MUST land behind an extra (`[openai]`/`[local-llm]`/`[vec]`/`[ui]`/`[server]`), import-guarded so core never imports it. Adding a core dep requires an ADR in `decisions.md`. **`fastapi`/`psycopg` in core = build break.** (`pyyaml` is a *dev/eval* dep — it lives in the `dev` extra, not core.)
- **Doctor is an invariant check, not just info.** It verifies `notes==fts==vec`, runs `PRAGMA integrity_check`/`quick_check`, and reports dead jobs, oldest-pending age, stale vectors, embedder dim, and the resolved UI port. **If you add a grain to the write txn, add it to the doctor invariant.**
- **Schema changes are additive + idempotent,** gated by `user_version`. Non-additive migrations auto-snapshot first. Never drop/rewrite v1 columns. "No migration" in SPEC = no manual user step, NOT no schema versioning. **Migrations must not break the offline default** (fresh install, no key, must `add`→`search`).
- **Never weaken an invariant to make a test pass.** If a test forces an invariant change, that needs an ADR, not a quiet edit. Failing CLOSED (BLOCK on ambiguity) is always the safe admission default.
- **Publish gate:** the project name (`cold-frame`, D19) and PyPI/trademark clearance are unverified. **No `twine upload` until that closes.**
