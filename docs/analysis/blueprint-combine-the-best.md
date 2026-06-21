# cold-memo: 조합 청사진 (Combine-the-Best Blueprint)

> 7종 메모리 시스템의 **실제 코드**를 해부한 뒤, 메커니즘별로 가장 잘 구현된 조각만 골라 **프로덕션급 개인 메모리 시스템**을 조립하기 위한 청사진.
> 위치: `소스 분석 → (이 문서) → 제품 설계`. 모든 권고는 `docs/source-analysis/`의 코드 분석(`file:line`)에 근거함. 작성 2026-06-21.
> 대상: 로컬 개인용으로 시작하되 *진짜 쓸모 있는 제품*을 지향(추후 출시 여지). 한 엔진을 통째로 가져오지 않고 **장점만 조합**.

---

## 0. 가장 큰 메타 발견 (왜 "조합 + 직접 구축"인가)

코드를 까보니 **거의 모든 시스템에서 "어려운 부분"(충돌해결 / 망각 / consolidation / 의미 dedup)이 실제로는 작동하지 않습니다.** ① 죽은 코드, ② stub, ③ 프롬프트/외부 라이브러리에 떠넘김 — 셋 중 하나입니다:

| 시스템 | "똑똑한 메모리 관리"의 실제 상태 (코드 기준) |
|---|---|
| **Mem0** | 2-phase ADD/UPDATE/DELETE는 **죽은 코드**(`prompts.py:176-460`). 라이브 경로는 ADD-only, dedup은 exact md5뿐(`main.py:898`) |
| **Graphiti** | 충돌/시간 모델은 **진짜 구현**(유일). 단 만료 edge가 기본 검색에 섞여 나옴(`search_filters.py:62-65`), write당 LLM 수십 콜 |
| **Letta** | 메모리 정확성을 **전적으로 LLM에 위임**. dedup/충돌 감지 없음, archival append-only, char limit 미강제(`block_manager.py:811-827`) |
| **Cognee** | 의미 충돌해결 **전무**. entity merge는 exact-name uuid5뿐, `update_version`은 죽은 코드(`DataPoint.py:242`) |
| **MemOS** | 마케팅 lifecycle/그래프 추론/write-merge **전부 죽은 코드/stub**(`relation_reason_detector.py:49-81`, `multi_modal_struct.py:534`), LoRA는 `b"Placeholder"` |
| **LangMem** | 메모리 지능 전부 3rd-party `trustcall`+프롬프트 1개에 위임(`extraction.py:253-260`). 코어 테스트 사실상 0 |
| **A-MEM** | `add_note`가 메타 추출을 **호출조차 안 함**(`memory_system.py:233-264`), index/ID 혼동 데이터 손상 버그, 생성자가 Chroma를 reset |

**결론:** 충돌해결·망각·consolidation·의미 dedup·token budgeting은 *어디서도 제대로 구현돼 있지 않다.* 따라서 전략은 명확하다 — **잘 만들어진 조각(추출 프롬프트, 하이브리드 검색, bi-temporal 모델, 티어드 dedup, 저장 규율)은 차용하고, 비어 있는 "메모리 두뇌"는 우리가 제대로 엔지니어링한다.** 그게 cold-memo의 정당한 가치이자 차별점이다.

---

## 1. 메커니즘별 "베스트" (구현 수준 비교 → 채택)

각 컴포넌트에서 *실제 코드가 가장 잘 된 곳*을 고르고, 약점은 보강한다.

### 1.1 메모리 단위 / 데이터 모델
- **개념의 베스트 — A-MEM**: 자기서술 atomic note = content + keywords + context + tags + links + temporal (`memory_system.py:24-81`). "자족적·링크된 원자 노트"는 개인 메모리에 맞는 멘탈 모델. 단 구현은 엉성(plain class, `links` 타입 거짓말, `evolution_history`/`retrieval_count` 죽은 필드).
- **스키마 엄밀성의 베스트 — MemOS**: `TextualMemoryItem` + 3계층 Pydantic 메타데이터 (`item.py:94-299`) — `status`, `version` + `history`(ArchivedTextualMemory 스냅샷), `sources`(provenance), `confidence`, `tags`, `covered_history`/`evolve_to` 계보.
- **통합 모델의 베스트 — Cognee**: `DataPoint` (`DataPoint.py:27-63`) 하나가 relational+graph node+vector source를 겸함. `Annotated[str, Embeddable()/Dedup()]` 마커로 index/identity 필드 자동 도출.
- **채택:** A-MEM의 *노트 개념* + MemOS의 *Pydantic 메타(status/version/history/provenance/confidence)* + Cognee의 *index_fields/identity_fields 에르고노믹스*. **content만 임베딩**, 메타는 옆에. 단 — A-MEM처럼 plain class로 두지 말고 진짜 Pydantic 모델로; links를 **실제 edge로 materialize**(A-MEM은 끝내 안 함).

### 1.2 Write — 추출(extraction)
- **베스트 — Mem0**: 단일 LLM 콜 additive 추출(`ADDITIVE_EXTRACTION_PROMPT`, `prompts.py:468-944`) — 자족적 사실(15–80단어), Observation Date 기준 시간 grounding, 날조/echo 금지. **금광: UUID→정수 remap 반환-환원**(`main.py:815-820`)으로 LLM이 긴 UUID를 환각하는 버그 클래스 제거.
- **차용 — Graphiti**: anti-generalization 규칙(`extract_edges.py:94-176`) — "Gamecube를 gaming console로 일반화 금지", 브랜드/숫자/색 보존.
- **차용 — MemOS**: 추출 시 `memory_type` 분류(LongTerm/User…) (`mem_reader_prompts.py:1`).
- **채택:** mem0식 단일콜 추출 프롬프트 + UUID→int remap + Observation-Date grounding; Graphiti의 anti-generalization 규칙 이식; MemOS식 memory_type 분류. 배치 임베드/삽입 + 단계별 fallback(`main.py:798-1081`) 패턴 그대로.

### 1.3 Write — dedup
- **베스트 — Graphiti**: 티어드 dedup(`dedup_helpers.py:220-279`) — exact-normalized-name → **엔트로피 게이트**(`:52-85`, 짧고 저정보 이름은 fuzzy 차단) → MinHash/LSH/Jaccard(0.9) → 모호한 것만 *배치 1콜* LLM. 결정적이고 저비용.
- **차용 — Cognee**: `uuid5(normalized_name)` exact-name 즉시 병합 fast-path(`generate_node_id.py`).
- **반면교사 — Mem0**: write dedup이 exact md5뿐(`main.py:898`) → "likes pizza" vs "loves pizza" 안 잡힘.
- **채택:** Cognee uuid5 exact fast-path → Graphiti 엔트로피게이트+MinHash → 모호분만 배치 LLM. 여기에 **의미 near-dup**(임베딩 코사인 임계) 한 단계 추가 — 아무도 write-time에 제대로 안 하는 부분.

### 1.4 Write — 충돌 해결 / 시간 모델  ⭐ (Graphiti가 유일하게 진짜 구현)
- **베스트 — Graphiti**: bi-temporal edge(4 타임스탬프 + reference_time, `edges.py:271-282`) + **비파괴 invalidation**(만료 표시만, 삭제 안 함, `edge_operations.py:538-573`) + **dual-candidate-set 충돌 프롬프트**(중복후보+무효화후보를 연속 인덱스로 한 콜에, `dedupe_edges.py:43-100`) + **결정적 시간 규칙**(들어온 정보가 더 오래된 거면 새 edge를 오히려 만료, `:826-839`).
- **차용 — MemOS**: `status='archived'` soft-delete + `covered_history` + ArchivedTextualMemory 버전 스냅샷(`feedback.py:307-316`).
- **채택:** Graphiti bi-temporal + 비파괴 invalidation + dual-candidate 프롬프트 + MemOS soft-archive/version history. **철칙: "무엇이 최신인가"를 LLM에 맡기지 말 것** — valid_at 비교 등 결정적 코드로(이전 조사의 max-over-serial과 동일 원칙).

### 1.5 Read — 검색(retrieval)
- **베스트(단순/관찰가능) — Mem0**: 3신호 하이브리드(semantic + BM25 sparse + entity boost, `main.py:1488`), capability-probe로 미지원 store에서 조용히 semantic-only로 degrade(`base.py:68`). **차용 가치 큰 디테일**: 쿼리길이 적응형 BM25 시그모이드 정규화(`scoring.py:16-54`), entity-boost **promiscuity 다운웨이팅** `1/(1+0.001*(n-1)^2)`(`main.py:1588-1668`)로 'User' 같은 허브 엔티티 억제.
- **베스트(구조적) — Graphiti/MemOS**: scope별 병렬 멀티메서드 fan-out + 합집합(`search.py`, `recall.py:35-138`).
- **채택:** 병렬 하이브리드 fan-out(semantic + BM25 + entity/graph). mem0의 적응형 BM25 정규화 + entity promiscuity 다운웨이트 차용. **융합은 mem0의 가산식(전역 divisor footgun, `scoring.py:94-119`) 대신 Graphiti의 RRF**(파라미터 프리, 이종 신호 보정 불필요).

### 1.6 Read — 랭킹/reranking
- **베스트 — Graphiti**: RRF + MMR + cross-encoder, 그리고 **LLM-as-cross-encoder(True/False + logprobs)**(`openai_reranker_client.py:61-118`)로 전용 모델 없이 rerank.
- **차용 — MemOS**: HTTP BGE reranker + 메타데이터 곱셈 boost `score*=(1+weight)` clamp[0,1](`http_bge.py:287`)로 recency/scope 편향.
- **채택:** 기본 융합 RRF; 옵션 cross-encoder rerank(self-host면 BGE, API-only면 LLM-boolean). recency/scope 메타 boost.
- **주의(반면교사):** Graphiti MMR은 O(n²) 덴스 행렬(`search_utils.py:1901`), cross-encoder는 passage당 1콜 — 후보 수 작을 때만. `node_distance`는 1-hop만 보는 가짜 최단거리(`:1816-1857`).

### 1.7 Read — token budgeting  ⚠️ (아무도 안 함 → 우리가 소유)
- **현실:** mem0/graphiti/letta/cognee/MemOS/langmem **전부 token-budget 패킹이 없다.** 모두 top_k 객체만 반환, 잘라내기는 호출자 몫.
- **채택:** **명시적 token-budget 패커를 직접 구현** — 랭킹 상위를 토큰 cap에 맞춰 채우기(Letta의 context paging 개념을 명시적 budgeter로). 이게 "context를 실제로 쓰는" 단계라 품질 직결인데 비어 있는 칸.

### 1.8 망각 / consolidation  ⚠️ (거의 비어 있음 → 우리가 소유, cold-memo 핵심)
- **부분 구현들:** MemOS 용량 기반 FIFO 캡(WorkingMemory=20, LongTerm=1500, `manager.py:74-80`); Letta LLM 요약/compaction(`compact.py`, 0.9*context_window 트리거); Cognee feedback EMA(`apply_feedback_weights.py`, α=0.1) — 단 랭킹만 바꾸고 삭제는 안 함.
- **연구 출처(이전 조사):** MemoryBank `R=e^(−t/S)` 망각곡선; Generative Agents `recency+importance+relevance`.
- **채택(조합):** 용량 캡(MemOS) + recency/importance decay 스코어(MemoryBank/GenAgents) + **백그라운드 reflection consolidation**(Letta sleep-time + langmem debounce) + status-flip soft-archive(MemOS) + bi-temporal invalidation(Graphiti) + feedback EMA 유용도 신호(Cognee). 에피소딕→시맨틱 요약 후 cold 강등.

### 1.9 저장 스키마
- **베스트(prod 규율) — Letta**: polyglot, **SQL을 source of truth + vector를 동일 PK로 dual-write**(벡터 장애가 데이터 손실 안 됨, `passage_manager.py:586-632`), 블록 버전 히스토리(`block_manager.py:842`), org-scoped 접근, 커서 페이지네이션.
- **개인 스케일 현실:** 로컬 우선 = SQLite(SoT) + 임베디드 벡터(sqlite-vec / LanceDB).
- **채택:** 로컬 우선 SQLite(SoT) + 임베디드 벡터 인덱스(동일 id dual-write, Letta 패턴) + 버전 히스토리 테이블. 추후 Postgres+pgvector로 스케일. **그래프 DB는 멀티홉이 진짜 필요해지기 전엔 도입 안 함** — 벡터 + SQL의 경량 edge로 시작(Neo4j 운영비/주입위험 회피).

### 1.10 스코핑 / 프로비넌스
- **베스트 — Cognee + MemOS + Graphiti**: 모든 메모리에 provenance(source 메시지/문서, 타임스탬프, content hash). Cognee `source_*` 재귀 스탬핑, MemOS `SourceMessage`, Graphiti `episodes[]` + MENTIONS(안전한 scoped 삭제 가능).
- **채택:** 메모리마다 provenance 1급. scope 키(user/agent/session)는 mem0식. 개인/단일유저로 시작하되 **scope 키는 설계에 미리 넣어둠**(나중 멀티유저/출시 대비).

### 1.11 백그라운드 처리 (write를 hot path에서 분리)
- **베스트 — Letta + LangMem**: Letta sleep-time 에이전트(N턴마다, "넌 primary가 아니다" 프레이밍, `sleeptime_multi_agent_v4.py:132-267`); LangMem `ReflectionExecutor` thread별 debounce/cancel(`reflection.py:254-329`).
- **채택:** debounce 있는 async consolidation 워커(LangMem) + reconciler 패스(Letta sleep-time 개념)가 dedup/merge/decay/consolidation을 hot path 밖에서. **단 둘 다 fire-and-forget·무재시도(landmine) → 우리는 durable 큐로.**

### 1.12 프롬프트 설계 패턴 (그대로 베낄 것)
- mem0: UUID→int remap; Graphiti: anti-generalization + dual-candidate 충돌 + standalone timestamp 추출(`extract_edges.py:242-271`); MemOS: fact-unit 분해 merge 프롬프트(`mem_reader_prompts.py:944`); LangMem: 자기수정 프롬프트용 **var-healer**(f-string 변수 mask→edit→unmask + 누락 시 hard-fail, `utils.py:165-248`) — 프롬프트 자가최적화 도입 시 필수 안전망.

---

## 2. 조합 청사진 (레퍼런스 아키텍처)

```
                          ┌──────────────────────────────────────────────┐
  ingest (chat/docs) ───▶ │ ASYNC WRITE WORKER (durable queue, hot-path밖) │
                          ├──────────────────────────────────────────────┤
                          │ 1. EXTRACT  ── mem0 단일콜 프롬프트 + UUID→int   │
                          │              remap + Observation-Date          │
                          │              + Graphiti anti-generalization    │
                          │              + MemOS memory_type 분류           │
                          │ 2. DEDUP    ── Cognee uuid5 exact → Graphiti    │
                          │              엔트로피게이트+MinHash → 의미 near-dup │
                          │              → 모호분만 배치 LLM                  │
                          │ 3. CONFLICT ── Graphiti bi-temporal invalidation│
                          │              (비파괴) + dual-candidate 프롬프트   │
                          │              + 결정적 freshness(LLM 금지)         │
                          │              + MemOS status=archived/version hist│
                          │ 4. PERSIST  ── 동일 id로 SQLite(SoT)+벡터 dual-  │
                          │              write + provenance 스탬핑 + scope키 │
                          └──────────────────────────────────────────────┘
                                              │
   ┌──────────────── STORAGE (로컬 우선) ──────┼──────────────────────────┐
   │ SQLite = source of truth (note + version_history + edges + provenance)│
   │ Vector = sqlite-vec / LanceDB (content 임베딩, dual-write 동일 id)      │
   │ (옵션) graph = 멀티홉 필요 시에만; 초기엔 SQL edge로 대체                  │
   └──────────────────────────────────────────────────────────────────────┘
                                              │
   query ──▶ ┌──────────────── READ PATH ─────┴───────────────────────────┐
             │ a. scope/temporal 필터 (만료·archived 기본 제외 — Graphiti footgun 회피)│
             │ b. 병렬 하이브리드 fan-out: semantic + BM25(적응형 시그모이드) │
             │    + entity boost(promiscuity 다운웨이트)                      │
             │ c. RRF 융합 (전역-divisor 회피) → 옵션 cross-encoder/BGE rerank │
             │    + recency/scope 메타 boost                                  │
             │ d. ⭐ TOKEN-BUDGET 패커 (아무도 안 하는 칸 — 우리가 구현)        │
             └────────────────────────────────────────────────────────────┘
                                              │
   ┌─────────── BACKGROUND (debounce, durable, Letta sleep-time 개념) ───────┐
   │ recency/importance decay (MemoryBank R=e^(−t/S) + GenAgents 스코어)      │
   │ → consolidation(에피소딕→시맨틱 요약, cold 강등) → soft-archive(status flip)│
   │ → feedback EMA 유용도 → 용량 캡(MemOS) GC                                │
   └────────────────────────────────────────────────────────────────────────┘
                                              │
   ┌─────────── EVAL HARNESS (공개 벤치가 안 재는 것 — 필수) ──────────────────┐
   │ 추출 품질 · dedup 정확도 · freshness/충돌 · forgetting · retrieval precision│
   │ · cross-scope 누출.  (LoCoMo/LongMemEval은 보조)                          │
   └────────────────────────────────────────────────────────────────────────┘
```

### per-component 채택표 (요약)

| 컴포넌트 | 채택 접근 | 차용 | 회피 |
|---|---|---|---|
| 노트/데이터 모델 | Pydantic 자기서술 노트, content만 임베딩, links=실제 edge | A-MEM 개념 + MemOS 메타 + Cognee index/identity 마커 | A-MEM plain class·죽은 필드 |
| 추출 | 단일콜 additive + UUID→int remap + 분류 | mem0 + Graphiti + MemOS | — |
| dedup | uuid5 exact → 엔트로피+MinHash → 의미 near-dup → LLM | Graphiti + Cognee | mem0 md5-only |
| 충돌/시간 | bi-temporal 비파괴 invalidation + 결정적 freshness | Graphiti + MemOS | LLM에 최신성 위임 |
| 검색 | 병렬 하이브리드 + RRF | mem0 신호 + Graphiti 융합 | mem0 전역 divisor |
| rerank | RRF 기본, 옵션 cross-encoder/BGE + 메타 boost | Graphiti + MemOS | O(n²) MMR 남용 |
| token budget | **직접 구현** | (Letta 개념) | 전원 미구현 |
| 망각/consolidation | 캡+decay+백그라운드 consolidation+soft-archive | MemoryBank/GenAgents/MemOS/Letta/Cognee | A-MEM 무한성장 |
| 저장 | SQLite SoT + 벡터 dual-write 동일 id + 버전 히스토리 | Letta | A-MEM RAM/Chroma 드리프트 |
| 프로비넌스/스코프 | 메모리마다 provenance + scope 키 선설계 | Cognee/MemOS/Graphiti | — |
| 백그라운드 | debounce + **durable 큐** | LangMem + Letta | fire-and-forget 무재시도 |

---

## 3. 프로덕션 경고 — 실제 코드에서 본 지뢰(우리가 안 밟을 것)

1. **만료/무효 메모리를 기본 검색에서 반환 금지** — Graphiti의 가장 미묘한 footgun(`search_filters.py:62-65`). temporal 필터를 read에 기본 적용.
2. **freshness를 LLM에 위임 금지** — 결정적 코드로(valid_at/version).
3. **exact-string dedup만 두지 말 것** — mem0 md5(`main.py:898`)는 의미 중복을 통과시킴.
4. **content 전체를 한 passage로 저장 금지** — Letta `passage_manager.py:566`는 청킹 없이 통째 임베드(토큰 초과). 청킹 필수.
5. **SoT와 벡터 인덱스 드리프트 금지** — A-MEM(RAM↔Chroma 수동 동기, 생성자 reset)·Letta(TPUF 에러 swallow) 둘 다 조용히 갈라짐. 동일 PK dual-write + 재조정 + 실패 시 시끄럽게.
6. **표시 인덱스를 ID로 LLM에 넘기지 말 것** — A-MEM의 데이터 손상 버그(`memory_system.py:308,687-716`). 안정 ID + remap.
7. **fire-and-forget consolidation 금지** — Letta/LangMem 둘 다 무재시도(프로세스 죽으면 그 턴 메모리 유실). durable 큐 + 재시도.
8. **랭킹 위해 그래프 전체를 메모리에 적재 금지** — Cognee `brute_force_triplet_search.py:277`. 랭킹은 DB/ANN으로.
9. **limit을 메타데이터로만 두지 말 것** — Letta 블록 char limit이 write에서 미강제(`block_manager.py:811`). write 시 강제.
10. **sync/async 코드 중복 피하기** — mem0(~1500줄×2)·langmem 드리프트 원인. 단일 구현.
11. **SQL/Cypher를 f-string 보간으로 만들지 말 것** — MemOS 주입 위험(`neo4j.py:208-220`). 파라미터 바인딩.
12. **코어를 결정적으로 테스트** — A-MEM(실 OpenAI 호출, 'not None'만 검증)·langmem(코어 테스트 ~0). LLM mock + 실제 결과 검증.

---

## 4. 설계 단계로 넘어가기 전 미해결 질문

1. **노트 입자(granularity)**: mem0식 atomic fact vs A-MEM식 note vs Graphiti/cognee식 graph triple — 무엇을 1급 단위로? (개인 메모리엔 atomic note + 경량 edge가 유력)
2. **그래프 DB 도입 시점**: 멀티홉 추론이 실제 필요한가? 아니면 벡터 + SQL edge로 충분한가? (초기엔 후자 권장)
3. **write 경로의 "agentic" 정도**: Letta식 LLM self-edit(유능한 에이전트 루프 필요) vs mem0식 파이프라인 추출(결정적, 개인용에 유리) — 무엇을?
4. **임베딩 모델 + 크로스티어 일관성**: hot/warm/cold가 같은 vector space를 공유해야 하며, 재임베딩 마이그레이션은 경로무관 락인 비용.
5. **procedural memory 포함 여부**: LangMem식 *행동* 메모리(시스템 프롬프트 자가최적화, `gradient.py`)도 넣을까, 아니면 사실/시맨틱만?
6. **sync vs async write**: async가 지연엔 유리하나 durable 큐 필요 — 개인 로컬에서 어느 수준까지?
7. **eval harness 설계**: 무엇을 측정? (추출 품질·dedup·freshness·forgetting·retrieval precision·cross-scope 누출 — 공개 벤치가 안 재는 것 중심)
8. **스코프 정책**: 단일 유저로 시작하되 멀티테넌트 키를 어디까지 미리 설계?

---

### 차용 출처 한눈에
- **Mem0** → 추출 프롬프트, UUID→int remap, 적응형 BM25 정규화, entity promiscuity 다운웨이트, 배치+fallback 파이프라인
- **Graphiti** → bi-temporal 비파괴 invalidation, dual-candidate 충돌 프롬프트, 티어드 dedup(엔트로피+MinHash), RRF 융합, LLM-as-cross-encoder, provenance
- **Letta** → SQL-SoT+벡터 dual-write(동일 PK), 블록 버전 히스토리, sleep-time 백그라운드 reconciler, 정밀 str-replace 에디터
- **Cognee** → DataPoint 통합 모델·index/identity 마커, uuid5 exact fast-path, feedback EMA, 재귀 provenance, dataset-scoped 삭제
- **MemOS** → status-as-filter soft-delete, version history 스냅샷, 병렬 멀티-recall, 메타 boost rerank, fact-unit merge 프롬프트
- **LangMem** → 변경시에만 write(diff), debounce-by-thread 백그라운드, var-healer, procedural(프롬프트 자가최적화) — 도입 시
- **A-MEM** → Zettelkasten 노트 *개념*, link+neighbor-update를 한 콜에 융합하는 프롬프트 골격(단 실제 ID로 재구현)
- **연구(이전 조사)** → MemoryBank `R=e^(−t/S)`, Generative Agents recency+importance+relevance, deterministic freshness(max-over-serial)
