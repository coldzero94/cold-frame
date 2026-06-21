# Coldframe TDD Plan

# Coldframe P1 TDD 계획 — red/green 순서 · 불변식-as-tests · mock/fixture 전략

> P1(골격: store + models + add/search + 최소 MCP + eval 하버스)을 위한 테스트-우선 빌드 계획.
> **전제:** `CLAUDE.md §0`의 6개 pre-P1 게이트(G1 sync · G2 quarantine 표현 · G3 LLM/Embedder ABC · G4 Store ABC · G5 상수 · G6 Clock/RNG)를 먼저 비준한 뒤에야 STEP 1을 시작한다. 게이트를 잘못 추측하면 엔진 전체 + golden-set 재작업이 발생한다. 전체 본은 `docs/tdd-plan.md`.

---

## 0. 프레임워크 · CI 티어

- **pytest.** 단위 테스트는 `tests/`(`test_store_txn.py`, `test_jobs_queue.py`, 모듈별 write/read/cli/mcp), 엔진 동작은 데이터-드리븐 eval 하버스 `cold_frame/eval/harness.py`가 `cold_frame/eval/datasets/*.yaml`을 `@pytest.mark.parametrize`로 케이스당 1 pytest로 펼친다(실패 입도 = 케이스 단위).
- **CI 2티어:** `tests-core`(모든 PR, 네트워크/키 0, mock-LLM + HashEmbedder 전 suite = **R16/R17 머지 게이트**) / `evals-live`(nightly, 키 필요, non-blocking).
- **마커:** `slow`(perf, `tests/perf/`, 머지 게이트 제외), `live`(키 필요). 머지 게이트 = `pytest -m "not slow and not live"`.
- `pytest.raises`로 fail-closed/예외, log-capture fixture로 "로그에 content 없음"을 검증. Store under test = **실제 SQLiteStore**(`:memory:`/`tmp_path`) — txn/integrity/jobs는 실드라이버 필요, mock 금지.

---

## 1. Mock / Fixture 전략 (결정적, 네트워크 0)

4개 주입 seam:

1. **`ScriptedLLM(LLM)`** — 정식 mock. `is_local=True`. YAML `llm_script`로 구성, `(task:TaskTag, match)` 키잉(`match ∈ {contains:s}|{seq:n}|{any:true}`). 구체 match 1회 소비, `{any:true}` 재사용·최저우선. `self.calls` 기록(leak 단언). **미매칭 호출 ⇒ hard `EvalError`**(모든 LLM 상호작용 선언 강제). `schema!=None` → `LLMResult(parsed=schema.model_validate(...))`.
2. **`RecordingRemoteLLM(LLM)`** — `is_local=False`, 호출 기록. I-LOCAL fail-closed + `test_no_secret_to_remote` 단언용. 추출 provider로 꽂아 admission이 secret을 원격 라우팅 안 함을 증명.
3. **`HashEmbedder`** — 기본 테스트 임베더(D4). `dim=256`, `blake2b(token)`→buckets, L2-norm → cosine 재현·유의미(`pizza~pizza > pizza≠pasta`). prod=테스트 동일 → 임베더 mock 불필요. `FixedVectorEmbedder`는 cosine-band(0.82/0.93) 경계용.
4. **`FrozenClock(Clock)`** — 엔진은 `clock.now()`만. 하버스가 `step.at`로 구동. tiebreak RNG는 `case.seed` 시드, eval UUID = `uuid5(NS, f"{case.id}:{ordinal}")`.

**Golden datasets:** family당 1 YAML(엄격 pydantic `Suite/Case/Step/Expect`). 케이스 = `llm_script` + 순서 steps(op + 주입 `at` + scope) + `expect`(notes status/invalid_at, supersedes edge, search top-hit content_like, as_of). 토큰 카운팅은 packer와 **같은 tokenizer**(cl100k_base 있으면 그것, 없으면 결정적 4-char/whitespace heuristic).

**Fixtures:** `frozen_clock` · `scripted_llm` · `recording_remote_llm` · `hash_embedder` · `fixed_vector_embedder` · `mem_store`(실 SQLiteStore, migrated) · `memory`(facade) · `log_capture` · `eval_case`(YAML parametrize) · `token_counter`(packer·budget 공유).

---

## 2. P1 red → green 순서

각 STEP: RED(먼저 실패) → GREEN(최소 통과). STEP 끝마다 `uv run pytest -m "not slow"` 전체 green.

| STEP | RED | GREEN |
|---|---|---|
| **1 models** | `test_models.py`: 모델 검증, 기본값(status='active', confidence=1.0, version=1), Status/MemoryType Literal이 잘못된 값 거부, timestamp tz-aware UTC | `models.py`(pydantic v2) + `exceptions.py`. `llm/base.py` LLM/Embedder ABC + `TaskTag` enum 고정 |
| **2 embedder + clock seam** | HashEmbedder 결정성(같은 텍스트→같은 벡터, dim=256, L2-norm; `cos(pizza,pizza)>cos(pizza,pasta)`); `FrozenClock.now()` 주입 시각 반환 | `llm/providers.py` HashEmbedder, `Clock`+`FrozenClock`, `eval/harness.py`의 `ScriptedLLM`/`RecordingRemoteLLM` |
| **3 store migrate + meta** | `migrate()` 멱등(2회 무에러), 전 테이블 생성, `embedder_meta(hash,256)` round-trip; vec dim==meta(1536 리터럴 없음) | `store/sqlite.py` migrate + meta + PRAGMA(WAL, busy_timeout, foreign_keys) |
| **4 atomic add_note + 단일-txn** | `test_store_txn.py`: notes+fts+vec+sources+history+events 1 txn; vec insert raise→전 ROLLBACK(모든 grain 0); provenance guard(active+conf≥0.4 + sources 0 → raise) | `add_note`(`BEGIN IMMEDIATE`), `in_transaction()`, `append_event`, provenance trigger/guard |
| **5 store 조회 primitive** | knn(brute-force cosine, embedder_id hard-filter, scope+status), bm25(FTS5 MATCH), get_notes(순서보존), set_status, by_status, touch/reinforce. 무매칭에 `[]`(raise 금지) | `store/vectors.py`, `fts.py`, `notes.py` |
| **6 WriteCore EXTRACT + offline** | `add(llm=None)` naive(1 메시지=1 fact)→search round-trip(**offline 불변식**); ScriptedLLM→N facts; durability gate가 ephemeral-low 드롭; conf<0.4→quarantine, 기본 search 제외 | `write/extract.py` + `write/core.py` commit(EXTRACT→ADMISSION→DEDUP→CONFLICT→PERSIST) |
| **7 ADMISSION secret BLOCK + I-LOCAL** | secret turn→`blocked=[secret]`,`added=[]`, DB grep 0; `raw=True`도 BLOCK; `assert_local_for('admission_tiebreak',RemoteLLM)`→`PolicyError`; RemoteLLM 추출자→calls에 secret span 0 | `write/admission.py`(regex+entropy) + `assert_local_for` fail-closed |
| **8 DEDUP + CONFLICT + 결정적 freshness** | dedup 양성→1/음성→2(재현); freshness t0 Vessl→t1 Anthropic `{contradiction}`→old archived(invalid_at=new.valid_at, expired_at=now, supersedes edge) 1 txn; **garbage hint 재실행→불변** | `write/dedup.py`(uuid5→MinHash→cosine) + `write/conflict.py`(LLM 제안; valid_at 비교+archive는 코드) |
| **9 READ: fan-out→RRF→budget→reinforce** | semantic+bm25 over-fetch, RRF 결정적(고정 rank→정확 순서, k=60); `as_of`가 status filter 우회+TRUE 술어; budget 준수+최상위-strength 포함; cross-scope leak=0; REINFORCE touch | `read/retrieve.py`, `fuse.py`, `budget.py` |
| **10 CLI** | `add/search/list/show/stats` offline round-trip; `--json` 파싱; `--as-of`, `--status`(G2 표현) 반영 | `prompts/cli.py`→Memory facade (pyproject entrypoint 일치) |
| **11 최소 MCP** | `search_memory`/`add_memory`가 §7 JSON(hits strength/band, ui deep-link); **blocked secret은 SUCCESS**(MCP error 아님); 모든 메서드 sync, mcp.py만 async | `prompts/mcp.py`(FastMCP)가 sync Memory 래핑 |

**P1 ACCEPTANCE** = extraction + precision@k + cross_scope green; offline add→search round-trip; `claude mcp add` 호출됨.

---

## 3. 불변식-as-tests

| # | 불변식 | 검증 아이디어 |
|---|---|---|
| I1 | Freshness=코드 | freshness.yaml: ScriptedLLM은 `{contradiction}`만→Anthropic active, Vessl archived. **반대/garbage hint 재실행→동일**(LLM이 newer 못 뒤집음) |
| I2 | Archive-not-delete | `forget(id)`→row 잔존+archived; `revive`→active. over-cap 저-imp가 archived(**row 잔존**) |
| I3 | 단일 txn(C3) | old row archived, invalid_at==new.valid_at, expired_at==주입 now, edge(new,old,supersedes); insert 후 raise→ROLLBACK으로 old active 유지·new 부재 |
| I4 | Sync core + 1 async seam | introspection: 전 public 메서드 `not iscoroutinefunction`; `async def` grep은 `prompts/mcp.py`에만. sync/async 중복 없음 |
| I5 | Offline 기본 | `test_offline_roundtrip`: `:memory:`, llm=None, 키 없음→add→search 회수, 네트워크 0 |
| I6 | Admission 디스크 전 BLOCK | secret→`blocked=[secret]`,`added=[]`, DB grep 0; `raw=True`·`create_fact`도 BLOCK; blocked에 secret substring 없음 |
| I7 | I-LOCAL | `assert_local_for('admission_tiebreak',Remote)`→`PolicyError`; Remote 추출자+secret→디스크 전 BLOCK + calls에 secret span 0(모든 task) |
| I8(as_of) | as_of가 status filter 우회 | `search(as_of=t0.5)`→top 'Vessl'(now-archived), `search()`→'Anthropic'(필터 우회 증명) |
| I9 | write grain 일관 | doctor: `notes==fts==vec==sources==history==events`; vec/fts raise→전 grain 0; happy path 모든 grain id 매칭 |
| I10 | Secret hard-purge(예외) | `test_purge_leaves_no_residue`: purge 후 notes,fts(+shadow),vec,history,sources,jobs.payload,events grep→0; tombstone(content='', deleted) 잔존 |
| I11 | provenance + pending 제외 | 저-conf/provenance-less→quarantine, 기본 search 0, `held_for_triage`/`by_status` 반환. active+conf≥0.4+sources 0→guard+trigger raise |
| I12 | Portable schema | grep(store/sqlite.py 제외): `INSERT OR REPLACE` 없음, non-iso utcnow 저장 없음, DDL `1536` 없음. migrate→meta.embedder_dim=256, vec dim==meta; KNN embedder_id hard-filter |
| I13 | RRF/dedup/budget 결정적 | budget=200→`sum(token_len)<=200`+최상위-strength 포함, 2회 동일. dedup 양성→1/음성→2. RRF 고정 rank→정확 순서(k=60) |
| I14 | Sync core 중복 없음 | introspection+grep(I4). 같은 로직 sync+async 이중 구현 부재 |
| I15 | Jobs durable queue | claim atomic lease; 2번째 claim 다른/none; LEASE_TTL 경과→stale reclaim→pending; fail N→max_attempts 후 dead; 같은 dedup_key 2회→1 row; 같은 content_hash 2회→DEDUP collapse |
| I16 | consolidate 비파괴·수렴 | 20 저-imp+5 pinned→consolidate→5 active, over-cap archived(잔존). `test_no_unbounded_growth`: 10k→`count(active,episodic)<=500`. 2회→2번째 no-op |
| I17 | 로그 secret 0 | `test_logs_have_no_content`: content에 sentinel→전 로그 캡처 부재. llm.call 로그는 task/model/is_local/token만 |
| I18 | correct_memory | old archived, invalid_at=now, edge(new,old,supersedes), history update_type='correct'. new_text secret→BLOCK(ADMISSION이 correction 경로에서도) |
| I19 | Self-edit=add 등가 | P6: dedup+freshness를 `memory_tools` 경로로→동일 결과(단일 WriteCore). create_fact secret도 동일 BLOCK |
| I20 | cross-scope isolation | scope A에만 add; `search(scope=B)`→0; knn·bm25 fan-out leak==0 |
| I21 | dim mismatch fail-fast | 256 DB를 1536로 재오픈→`EmbedderMismatchError`. `allow_reembed=True`→reembed job + KNN stale 제외(BM25는 반환) |
| I22 | var-healer hard-fail | EDIT가 `{user_name}` 누락→`VarHealerError`+version 불변. DIAGNOSE `warrants_adjustment=False`→`changed=False`, EDIT 호출 없음 |

---

## 4. 핵심 주의 (게이트 의존)

- **G2(quarantine 표현)** 미결 시 I11/STEP 6·10의 `pending` vs flag 단언이 갈린다. eval YAML `ExpectNote`까지 한 표현으로 sweep 후 STEP 6 RED 작성.
- **G3(Embedder 반환형)** `np.ndarray` vs `list[list[float]]` 미결 시 STEP 2·5 cosine/knn 시그니처가 갈린다(`np.ndarray` 권장).
- **G6(Clock/RNG)**를 STEP 1 전에 `Memory.__init__`에 주입해야 STEP 8(freshness)·decay가 결정적. 사후 retrofit은 엔진 전반 재작업.