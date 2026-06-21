# cold-frame — 구현 스펙 (SPEC)

> 👉 **코딩은 이 문서 하나만 보면 된다.** 배경/근거(왜 이렇게 정했나)는 `analysis/`(시장비교·build-vs-use·소스해부·언어결정·blueprint)와 [`decisions.md`](decisions.md)·[`requirements.md`](requirements.md)에.
> 상태: 기획. 코드는 기획 완료 후. 결정은 D-번호(→ decisions.md), 요구는 R-번호(→ requirements.md)로 추적.

## 0. 한 줄 + 결정 요약

로컬 우선·소유 가능한 LLM 에이전트 메모리 레이어. **단일 SQLite 파일** 하나에 사실+BM25+벡터+edge+버전+provenance가 다 들어가고, 외부 의존·키 없이 동작. 핵심 차별점 = 어디에도 제대로 없는 **token-budget 패커 + 효율적 저장(망각/consolidation) + 결정적 충돌해결**을 직접 구축.

| 결정 | 값 |
|---|---|
| 전략 (D1) | 하이브리드: 검증된 패턴 차용 + 빈 "메모리 두뇌" 직접 구축 |
| 언어 (D10) | **Python + uv**. `Store` 어댑터 시임 유지, hot path는 eval 병목 증명 후 이식 |
| 저장 (D3) | SQLite 단일파일 우선, 출시 시 Postgres 어댑터 추가. 스키마 portable |
| 패키징 (D4) | 코어/서버 하드 분리. 코어 deps = pydantic+numpy. 오프라인 기본 |
| 배포 (D5) | uv (로컬 Docker X). 컨테이너는 서버 레이어만 |
| 노트 단위 (D7) | atomic fact + 경량 SQL edge (그래프 DB는 멀티홉 필요 전까지 X) |
| Write (D8) | 하이브리드: 파이프라인 추출 + self-edit 도구, 공통 WriteCore |
| Procedural (D9) | v1 포함 (프롬프트 자가최적화) |
| Claude Code 연동 (D11) | MCP 서버. 로컬 stdio = OAuth 불필요. 원격 = OAuth 지원 |

---

## 1. 패키징 & 배포

- 코어 `cold-frame`: SQLite + 오프라인 기본 + 라이브러리 + CLI + MCP 서버. deps = `pydantic`,`numpy`만. `fastapi`/`psycopg` **import 금지**.
- extras: `[openai]`/`[local-llm]`(오프라인 실품질)/`[vec]`(sqlite-vec 가속)/`[ui]`(로컬 화면)/`[server]`(분리된 Postgres+FastAPI 제품 레이어).
- **원스톱 설치/셋업**:
  1. `uv tool install cold-frame` (또는 `pip install cold-frame` / `uvx cold-frame`) — 1 명령.
  2. (옵션, Claude Code 연동까지) `cold-frame setup` — DB 보장 + **Claude Code에 MCP 자동 등록**(`claude mcp add cold-frame -- cold-frame mcp`, 멱등, `--scope` 선택; `claude` 없으면 `.mcp.json` 작성/안내 fallback) + 상태 출력.
  - 한 줄: `uv tool install cold-frame && cold-frame setup`.
  - DB(`~/.cold-frame/memory.db`)는 첫 실행에 **자동 생성**(별도 마이그레이션 없음, 프로그램과 분리·백업=복사). 기본 **오프라인**(HashEmbedder)이라 키 없이 즉시 동작. **네이티브 확장 불요**(numpy KNN 기본; `[vec]`는 옵션 가속).
  - MCP 서버 진입점 = `cold-frame mcp`(콘솔 스크립트) → `python -m` 경로 이슈 없이 어디서나.
- 오프라인 보장: 기본 `HashEmbedder`(의존성 0, 결정적) → 키 없이 즉시 동작. 실품질은 `[openai]`/`[local-llm]`.
- 이식성 규칙: 방언 특화(FTS5/sqlite-vec/JSON)는 `Store` 어댑터 뒤로, ISO8601-UTC 타임스탬프, SQLite-전용 관용구 금지.

## 2. 데이터 모델 (R7·R8 / 입자 규칙 포함)

**노트 입자(D7 세부):** 1 노트 = **하나의 자족적 사실**(mem0식, 15–80자, 대명사/상대시간 해소된 self-contained). 복합 문장은 atomic 사실들로 분할. 엔티티는 content + keywords로 표현(P1엔 별도 노드 아님), 관계는 edge로.

```python
MemoryType = Literal["semantic", "episodic", "procedural"]
#   semantic   : 지속 사실/선호 ("coby는 다크로스트를 선호")
#   episodic   : 시점 있는 사건/경험 ("2026-06 Anthropic으로 이직")
#   procedural : 행동 지침 (프롬프트 조각, §7)
Status = Literal["active", "archived", "deleted"]  # 3-value (G2 비준)
# quarantine = status 값이 아니라 **flag 컬럼**으로 표현 (D22/G2): held_for_human/quarantined/triage_reason.
# 저신뢰/모순/provenance 부재 = quarantined=True 플래그 → 기본 검색 제외 (B4/D21 superseded by G2).

class Note:                       # Pydantic. content만 임베딩.
    id: str; content: str; memory_type: MemoryType
    keywords: list[str]; tags: list[str]; context: str; confidence: float
    scope: Scope                  # user_id/agent_id/session_id
    sources: list[Source]         # provenance: kind/ref/role/content_hash/observed_at
    status: Status; version: int
    created_at; expired_at; valid_at; invalid_at   # bi-temporal 4 타임스탬프 (R1)
    importance: float; last_accessed; access_count: int; decay_S: float  # 망각 신호 (R5)
```

**Edge relation 어휘(고정, 작게 시작):**
`supersedes`(충돌해결: 새→옛), `relates_to`(연관), `mentions`(엔티티 provenance), `derived_from`(consolidation: 요약→원본), `caused_by`(옵션). 확장 가능.

**SQLite 스키마:** `notes`(scope/status/4타임스탬프/decay 컬럼) + `note_fts`(FTS5: content/keywords/tags, `bm25()`) + `note_vec`(note_id, **embedder_id, dim**, embedding BLOB; dim은 활성 embedder에서 파생(하드코딩 금지), `knn()`은 embedder_id로 hard-filter; 기본 numpy KNN, `[vec]`면 sqlite-vec) (H1) + `edges`(src/dst/relation/weight/valid·invalid_at) + `note_history`(버전 스냅샷, update_type) + `sources` + `access_log`(note_id, ts — 망각곡선 full history; UI 시각화용 유일한 schema 추가, 없으면 현재 강도만으로 degrade) + `jobs`(durable 백그라운드 큐). 상세 DDL: [`analysis/design.md`](analysis/design.md) §2.3.

## 3. 저장 레이어

단일 `.db`. `Store` 인터페이스만 코어가 호출: `migrate()`, `add_note(note, emb)`(단일 트랜잭션: notes+fts+vec+sources+history), `knn(emb,k,scope,statuses)`, `bm25(query,k,scope,statuses)`, `get_notes(ids)`, `touch(ids)`, `add_edge`/`neighbors`. SQLiteStore가 기본 구현; 동일 인터페이스 뒤에 추후 PostgresStore. **SoT와 벡터는 같은 트랜잭션 dual-write**(드리프트 금지).
- **notes = SoT + co-written append-only 이벤트 로그 (D17/D21 B1=A)**: §4의 모든 in-place mutation은 그대로 유효; **같은 트랜잭션에서** 변경을 append-only `events`(device_id, lamport, content_hash, op, payload)에 co-write(materialized view 아님 — C1 해소). export=로그 덤프, future-sync는 같은 primitive. **live `.db` 파일 sync 금지**(Dropbox/iCloud가 SQLite+WAL 손상) — export/backup은 checkpointed read-only 스냅샷만. DDL/replay/동시성 상세 → [`build/data-layer.md`](build/data-layer.md), sync 입장 → [`product-strategy.md`](product-strategy.md) §2.

## 4. Write Path (R1·R2 / D8)

```
add(messages)──[EXTRACT]──┐                 self-edit 도구──┐
 mem0 단일콜 프롬프트        │                                │
 + UUID→int remap(환각방지)  │                                │
 + Observed-Date grounding  │                                │
 + anti-generalization      │                                │
 + memory_type 분류          └──────────────┬────────────────┘
                                            ▼  ★공통 WriteCore (R1·R2)
 [ADMISSION] (D15·D18, dedup 전·write 전) CLASSIFY→REDACT→CONFIDENCE-GATE→CONSENT
           secret/credential=BLOCK(디스크에 안 닿음, 무가치 tombstone만) · PII=REDACT(타입 placeholder, 원본 비영속) · 그 외 ALLOW
           durability gate: durable(identity/preference/decision)만 영속, ephemeral chatter drop, confidence<0.4→hold_for_human
 [DEDUP]   uuid5(normalized) exact → 엔트로피게이트+MinHash(0.9) → 의미 near-dup(코사인) → 모호분만 배치 LLM
 [CONFLICT] 같은 주제/endpoint 후보 검색 → dual-candidate 프롬프트(중복 vs 모순 한 콜)
           → 결정적 freshness(valid_at 비교, LLM 금지) → 모순이면 옛 status=archived + **invalid_at=new.valid_at + expired_at=now() (C3, 같은 txn)** + supersedes edge
 [PERSIST]  단일 트랜잭션 notes+fts+vec+sources+history (async durable jobs 큐 경유)
```
- 오프라인/`llm=None`: naive 추출(메시지=사실 1개) → 키 없이도 add 동작.
- 철칙: freshness는 코드로(LLM은 후보추출/모순판정만). 모순은 삭제 X, archive(비파괴).
- **Admission(D15)**: secret=gitleaks식 regex+Shannon엔트로피, PII=Presidio(NER+regex+context), user-editable allow/deny TOML. LLM 게이트는 모호 span만·**strictly local**(D4 키0 보존, LLM 단독 방어선 금지).
- **LLM 제안·코드 처분(D18)**: LLM은 sameness/모순 판정 + episodic→semantic 요약만; freshness·archive·merge-commit은 결정적 코드. conflict/merge LLM은 코사인 0.82~0.93 밴드만(§6 Triage 일치). **confidence(추출 확신) ≠ importance(장기 가치)** 별도 필드. provenance 없는 사실은 high-confidence 불가(quarantine).
- **stage 분할(D21 B7)**: 결정적 단계(EXTRACT·regex ADMISSION·uuid5/MinHash/cosine DEDUP)는 add() **inline·빠르게**. **LLM 단계(모호 admission tiebreak·0.82~0.93 conflict·batch dedup·요약)는 durable jobs 큐로 deferred**, 노트는 `pending`(provisional)로 입수 후 async 화해 → MCP `add_memory`가 Claude Code를 다중 LLM 왕복 블록 안 함. SLA/지연 예산 → [`build/eval-and-reliability.md`](build/eval-and-reliability.md).
- **프롬프트 전체**(추출/admission/충돌/병합/gradient/요약) → [`build/prompts.md`](build/prompts.md). **Admission이 remote extractor면 admission이 remote 콜 *전*에 실행**(redaction 순서, H10).

## 5. Read Path (R3·R4 / 검색·랭킹 세부)

```
search(query, scope, k, token_budget, as_of)
 1. FILTER  scope. as_of 없으면 기본 `status='active' AND NOT quarantined` (G2). **as_of 있으면 status 필터 bypass (C3)** + 2 temporal predicate(토글 D12):
            · TRUE     = valid_at<=as_of<invalid_at (사실이 참이던 시점)
            · BELIEVED = created_at<=as_of AND (expired_at IS NULL OR as_of<expired_at) (그때 우리가 믿던 것)
 2. FAN-OUT 병렬, 각 신호 over-fetch k*4:
            · semantic = note_vec KNN(코사인)
            · bm25     = note_fts MATCH, 쿼리길이 적응형 시그모이드 정규화
            · edge     = 매칭 노트/엔티티 1-hop 이웃 boost, promiscuity 다운웨이트 1/(1+0.001(n-1)²)
 3. FUSE    RRF(k_const=60) — 전역-divisor footgun 회피
 4. RERANK  (옵션, 기본 off) cross-encoder: [local-llm]=BGE / API=LLM-boolean+logprobs
            + recency/scope 메타 boost  score*=(1+w) clamp[0,1]
 5. BUDGET  token_budget 주어지면 상위부터 토큰 cap까지 패킹(노트 부분 truncation 정책). 미지정이면 top-k.
 6. REINFORCE 반환된 노트 access_count++, last_accessed=now, decay_S++ (회상 강화)
```
- decay/importance 스코어는 **읽기 랭킹이 아니라 §6 consolidation에서** 사용(읽기는 빠르게 유지).
- 결과 = `SearchResult{hits:[{note, score, signals}]}`.

## 6. 효율적 저장 = 망각 / consolidation (R5 / ★차별점)

> "기억만 무한 누적 X" — 백그라운드 `consolidate()`(durable jobs, debounce):
1. **decay 스코어** `score = w_r·e^(−Δt/decay_S) + w_i·importance + w_rel·relevance`. 회상 시 decay_S++로 강화. importance = 추출 시 추정 + feedback EMA(α=0.1).
2. **consolidation**: 같은 주제 episodic 클러스터 → 시맨틱 요약 노트 생성(`derived_from` edge), 원본은 cold 강등.
3. **soft-archive**: score < 임계 또는 capacity cap 초과 → `status='archived'`(삭제 X, 읽기 자동 제외).
4. **capacity cap**: type별(예: episodic 활성 N) 초과 시 최저 score부터 강등(결정적).
- durable 큐 + 재시도(fire-and-forget 금지). 수동 트리거도 가능.
- **Triage**: 엔진이 결정적으로 auto-resolve 못 한 것만 `held_for_human` 플래그 → UI Triage queue(§9). **진입 기준(확정)**: (a) 진짜 모순 — supersede 후보의 `valid_at` 동률이거나 시간 신호 부재로 최신 결정 불가, (b) 모호 병합 — 의미 코사인이 near-dup 임계(0.82)↑·auto-merge 임계(0.93)↓ 사이, (c) 저신뢰 추출 confidence<0.4, (d) pin 인접 auto-archive 후보(핀 근처는 자동 archive 금지). 그 외 전부 자동. 큐가 길면 impact(importance×최근성) 상위 N만, 나머지 'n more' 접기.
- **표시 강도 S & 글리프 밴드(확정, UI/ux §8.5)**: `S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))`, `retrievability = e^(−Δt_last_accessed / decay_S)`∈[0,1]. 밴드: S≥0.66 🌳evergreen · 0.33≤S<0.66 🌿budding · S<0.33 🌱fading. 단 confidence<0.4 또는 last_accessed>60d → at-risk(○) 강등(밴드 무관). pin은 밴드/decay 무시 상단 고정.

## 7. Procedural Memory (R7 / D9)

행동 메모리 = `memory_type="procedural"` 노트. `optimize_prompt(name, trajectory, feedback)`:
1. **diagnose**(gradient): think/critique/recommend → 실패 근거 없으면 `warrants_adjustment=False`(드리프트 방지 게이트).
2. **edit**: 제약 리라이트 + **var-healer**(f-string 변수 mask→edit→unmask, 누락 시 hard-fail).
저장/버전은 일반 노트 테이블/히스토리 재사용(롤백 가능). `get_procedural(name)`로 현재 지침 주입.

## 8. Claude Code 연동 (R9 / D11 — 검증됨)

MCP 서버 `cold_frame.mcp`(코어를 호출하는 얇은 어댑터).
- **로컬: stdio MCP → OAuth 불필요.** 원스톱 `cold-frame setup`(자동 등록) 또는 수동 `claude mcp add cold-frame -- cold-frame mcp` / `.mcp.json`. 서버 진입점 = `cold-frame mcp` 콘솔 스크립트.
- **원격(후): HTTP MCP → OAuth 2.0/2.1 지원.** `claude mcp add --transport http ...` + `/mcp`.
- 도구: `search_memory(query)`, `add_memory(fact)`, `summarize(topic)`, `list`/`timeline`. 모델이 tool-call.
- 컨텍스트 주입(옵션): SessionStart 훅 또는 MCP 리소스(`@cold-frame:...`). 기본은 tool-call.
- 네이티브 Claude Code 메모리(CLAUDE.md 등)와 상보적.

**MCP 도구 스키마(확정)**:
- `search_memory(query, k=8, scope?, as_of?, token_budget?)` → `{hits:[{id, content, memory_type, confidence, strength, status, sources:[{kind,ref,observed_at}], supersedes?}], used}` (recall-receipt 데이터)
- `add_memory(text, type?, source?)` → `{added:[{id,content}], superseded:[id], deduped}`
- `summarize(topic?, scope?, as_of?)` → `{summary, fact_ids:[]}` (관련 사실 모아 요약)
- `correct_memory(id, new_text)` → `{archived:id, new:{id,content}}` (bi-temporal in-place 교정)
- 리소스: `cold-frame://fact/{id}`, `cold-frame://recent` (@mention 가능)
- 모든 도구 결과에 `ui` deep-link(`http://localhost:27182/fact/{id}`) 포함 → 터미널/Claude Code→웹 점프

## 9. UX / 프로그램 화면 (R14·R18 / D12 — 상세: [`ux-design.md`](ux-design.md))

**핵심 원칙**: cold-frame의 무기는 토폴로지가 아니라 **상태(망각·신선도·믿음 변화)**. ⇒ **전역 그래프(hairball) 금지.** 그래프는 포커스 사실의 **로컬 1–2 hop ego 렌즈로만**.

- **히어로 인터랙션 = Belief-Fork × As-of Time-Travel** ("되감을 수 있는 믿음"): 항상 핀된 as-of 스크러버 → 뷰 전체가 그 시점 믿음-상태로 재구성(`valid_at<=as_of<invalid_at`, 엣지 동일). "내가 BELIEVED" vs "사실이 TRUE" 토글. 교정된 사실은 belief-fork(strikethrough 옛 → supersedes 화살표+원인 소스 → 하이라이트 새). 과거로 끌면 교정이 un-correct, 놓으면 snap-forward. 녹화가 아니라 **파라미터화된 as_of 쿼리 재구성**(bi-temporal+supersedes+note_history+decay 스키마라 가능 — 경쟁 도구는 구조적으로 불가).
- **프라이머리 surface = list/card "지금 내가 너에 대해 아는 것" 인스펙터**(Greenhouse 홈): 성장 단계 밴드(🌱/🌿/🌳, decay×conf×access 파생) + 강도/conf 미터 + FADING 밴드. **decay를 *보이게*** 하는 게 차별점의 전부(백엔드 점수로만 두지 않음).
- **작업 현장 = in-context recall-receipt**(Margin): MCP `search_memory`가 회상한 사실 + WHY(provenance) + 1키 correct/accept를 텍스트 카드로. see→trust→correct 루프를 현장에서 닫음.
- **3 surface, 1 코어/`.db`**: CLI=캡처·빠른 조회·스크립트 · MCP recall-receipt=작업 현장 · 웹 UI=큐레이션·트러스트·타임-트래블(읽기 중심, 경량 `[ui]` extra, 단일유저 — §1.2 제품 서버와 별개). TUI는 옵션.
- **불변 규칙**: archive-not-delete(항상 Revive) · 전이는 opacity/size만(reposition 금지=공간기억 보존) · 토스트는 믿음이 *변할 때만*(supersession/conflict). **예외(D16)**: secret/PII 플래그 사실은 archive-not-delete의 예외 = **hard-purge**(VACUUM+overwrite+grep 검증+증명). "forget+cascade"는 `derived_from` 엣지 따라 파생까지(일반=archive, secret=purge).
- **비주얼 미학(D14)**: **Dark Minimal + Life (Linear×Arc)** — 다크 우선, decay=빛/glow(강하면 밝게/바래면 dim), 절제된 글리프, 시그니처=as-of/belief-fork. 디자인 토큰·IA·플로우·상태 상세 → [`ux-design.md`](ux-design.md) Part 2·3.
- **웹 UI 스택(D20)**: **Vite + Vue 3(TS) + UnoCSS** SPA (antfu/skills 활용). `cold-frame ui`가 `[ui]` extra의 경량 ASGI로 **빌드된 정적 SPA + read-mostly JSON API**(ux §5.2 엔드포인트: active list / fact detail join / as-of snapshot / fork list / ledger)를 localhost 서빙. **빌드 번들을 패키지에 동봉 → 사용자는 Node 불필요**(pip/uv 원스톱 유지; pnpm/Vite는 우리 릴리스 빌드에서만). 코어/제품서버 분리(§1.2) 유지: UI 정적번들+thin read API ≠ 멀티테넌트 FastAPI+Postgres. 빌드 시 ux §8.9 가이드라인 체크리스트 적용.

**UI ↔ 빌드 단계 매핑**:
| 단계 | UI |
|---|---|
| P1 (CLI) | `show <id>`(provenance+버전+edge), `timeline`, `stats`; MCP recall-receipt(텍스트 카드) — Margin의 보장-출시 fallback |
| P3 (웹 UI) | `cold-frame ui`: Home(list/card) + Fact Detail(provenance 트레일+belief history+로컬 ego-edge) + **as-of 스크러버 v1** |
| P4 (망각) | FADING 밴드 + 망각곡선 sparkline + Pin/Let-go **Tend loop** + consolidation ledger + Triage queue — **decay 가시화가 진짜 히어로** |
| P5+ (옵션) | Constellation ego-별자리(살아있는 로컬 그래프, fixed compass) · Rewind Replay 타임랩스(온보딩 wow). 세컨더리 진단으로만 |

### 9.1 CLI 명령 범위 (R14)

| 명령 | 동작 | 주요 옵션 |
|---|---|---|
| `add "<text>"` | 사실 추출·저장(파이프라인) | `--type` `--user/--session` `--source` `--raw`(verbatim) `--observed` |
| `search "<q>"` | 하이브리드 검색 | `-k` `--budget` `--as-of` `--scope` `--json` |
| `recall "<q>"` | search를 recall-receipt 카드로(§9 화면 D) | (search와 동일) |
| `list` | 사실 목록(성장 밴드/글리프) | `--type` `--status` `--sort decay\|recent\|importance` `--fading` `-n` |
| `show <id>` | Fact Detail(강도·provenance·belief history·edge) | `--json` |
| `stats` | type/status 카운트·총량·fading·미해결 충돌 | |
| `timeline` | 시계열 뷰 | `--as-of` `--topic` |
| `tend` | 인터랙티브 triage(fading/충돌 → pin/let-go/merge/resolve) | |
| `pin\|forget\|revive\|edit\|merge <id>` | 큐레이션(forget=archive, edit=versioned, 모두 비파괴) | |
| `consolidate` | 지금 consolidation 실행 | |
| `ui` | 로컬 웹 UI 실행 | `--port` `--no-open` |
| `mcp` | MCP stdio 서버(Claude Code용) | |
| `setup` | 원스톱(DB + Claude Code MCP 등록) | `--scope` `--no-mcp` |
| `doctor` | 설치/DB/임베더/claude-mcp 상태 점검 | |
| `export\|import` | 포터빌리티(json/md) | |
| `config` | 임베더/LLM provider·기본 scope 설정 | |
| `path` | db 경로 출력 | |

출력 디자인: rich 테이블 + 상태 글리프(●strong ◐fading ○at-risk, 🌱🌿🌳) + recall-receipt 카드. `--json`은 모든 read 명령에서 스크립트용. 상세 사용자 플로우/비주얼은 [`ux-design.md`](ux-design.md).

## 10. Eval Harness (R16·R17 / 케이스 설계)

코어는 **LLM mock으로 결정적** 단위테스트. 측정 항목 & 초기 케이스:

| 측정 | 케이스 |
|---|---|
| 추출 품질 | 골든 대화→기대 사실 셋, precision/recall |
| dedup | 의미 중복쌍(양성: "likes pizza"/"loves pizza"; 음성: 별개 사실) → 병합/오병합 |
| **freshness/충돌** | 시계열("works at X"→"switched to Y") → 옛 archived·새 active, `as_of`가 시점값 반환 |
| **forgetting** | 저importance N + 고importance M → consolidate 후 고 retained / 저 archived |
| retrieval precision@k | 쿼리→기대 note id |
| cross-scope 누출 | user A 쿼리가 user B 노트 0개 반환 |
| token budget | 결과 ≤ budget & 상위 포함 |
| (보조) | LoCoMo/LongMemEval 어댑터 |

> **철칙**: 코어(dedup/conflict/decay)는 **LLM mock으로 결정적** 단위테스트(A-MEM·langmem이 안 해서 망한 부분).

**구체 케이스 예시(확정, LLM mock 결정적)**:
- 추출: in=`user:"I moved from Vessl to Anthropic last month"` → 1 fact `{~"works at Anthropic", episodic, valid_at≈observed−1mo}` + supersede 후보 탐지.
- dedup 양성: "I like pizza" → "I love pizza" ⇒ 1 노트(near-dup 병합). 음성: "I like pizza" + "I like pasta" ⇒ 2 노트.
- freshness: t0 "works at Vessl"(valid t0) → t1 "joined Anthropic"(valid t1) ⇒ search now: active="Anthropic", "Vessl" archived(invalid_at=t1); `search(as_of=t0.5)` ⇒ "Vessl".
- forgetting: 20 low-importance + 5 pinned/high → consolidate ⇒ pinned 5 active 유지, low 중 cap 초과분 archived(삭제 X, revive 가능).
- cross-scope: user=A 노트만 존재, `scope=B` search ⇒ 0 hits.
- token budget: 50 hits, budget=200 ⇒ 결과 토큰합 ≤200 & 상위 strength 포함.

## 11. 빌드 단계 (각 단계 acceptance)

| Phase | 산출물 | Acceptance |
|---|---|---|
| **P1 골격** | store(단일.db) + models + `add`(추출) + `search`(하이브리드+RRF) + CLI + 최소 MCP 서버 + eval 시작 | 키 없이 `add`→`search`로 방금 넣은 사실 회수. `claude mcp add`로 도구 호출됨 |
| **P2 정확성** | dedup(티어드) + bi-temporal conflict + 결정적 freshness + provenance/버전 | freshness/충돌·dedup eval 통과 |
| **P3 읽기품질+화면** | token-budget 패커 + rerank(옵션) + 메타 boost + 로컬 웹 UI | budget eval 통과, `cold-frame ui`로 기록 시각화 |
| **P4 망각** | decay + consolidation + durable 워커 + capacity cap | forgetting eval 통과, 무한성장 없음 |
| **P5 procedural** | gradient 최적화 + var-healer | 프롬프트 자가개선 + 변수 보존 |
| **P6 agentic write** | self-edit 도구(공통 WriteCore) | 도구 경로도 dedup/conflict 통과 |

> P1+P2만으로 mem0/Letta보다 충돌해결이 제대로 되는 개인 메모리. P3·P4가 진짜 차별점.

## 12. 디렉터리 구조

```
cold_frame/
  models.py  api.py(Memory: add/search/consolidate/optimize_prompt)
  store/   sqlite.py notes.py vectors.py fts.py edges.py     # Store 인터페이스 + SQLite
  write/   extract.py dedup.py conflict.py core.py tools.py
  read/    retrieve.py fuse.py rerank.py budget.py
  forget/  decay.py consolidate.py worker.py
  procedural/ optimize.py
  llm/     base.py providers.py     # Embedder/LLM ABC + Hash(기본)/OpenAI/Ollama
  prompts/ cli.py mcp.py            # MCP stdio 서버
  ui/      (P3, [ui] extra: 로컬 웹 대시보드)
  eval/    harness.py datasets/
pyproject.toml  README.md
```

## 15. Reliability & 동시성 (C4 / D21 B6·B7)
- **멀티프로세스 1 `.db`**: CLI + (세션별)MCP 서버 + UI 서버 + consolidation 워커 동시 접근. **WAL + `busy_timeout`(~5s) + foreign_keys ON**. **LLM 콜은 write txn 밖에서**(lock 보유 금지).
- **워커**: 단일 인스턴스(lockfile). jobs 상태머신 `pending→leased→done/failed→dead`(lease ttl+heartbeat, stale reclaim, attempts cap→dead-letter, 크래시 안전·idempotent).
- **동기성(B6)**: **sync 코어 + 얇은 `to_thread` async facade**(로직 중복 0). thread당 커넥션 1 + 소형 풀. LLM I/O만 진짜 async. MCP 서버가 유일 async seam.
- **마이그레이션(H2)**: `user_version` 게이트 additive·idempotent, non-additive 전 auto-snapshot, v1 drop/rewrite 금지. (§1 "마이그레이션 없음"=*사용자 수동 스텝* 없음을 뜻함, 스키마 버저닝은 함.)
- **부분쓰기(H4)**: notes/fts/vec/sources/history/events 전부 단일 txn. doctor invariant: notes==fts==vec(+reindex 복구). canonical 벡터=BLOB, [vec]는 그 위 인덱스.
- 상세 → [`build/data-layer.md`](build/data-layer.md) · [`build/eval-and-reliability.md`](build/eval-and-reliability.md).

## 16. 빌드 스펙 (focused docs) & 보안
코딩 시 SPEC와 함께 보는 구체 스펙:
- [`build/prompts.md`](build/prompts.md) — 6개 LLM 스텝 프롬프트 + 출력 JSON 스키마
- [`build/data-layer.md`](build/data-layer.md) — 전체 DDL·이벤트로그·동시성·migration·재임베딩
- [`build/read-and-budget.md`](build/read-and-budget.md) — retrieve→RRF→rerank→budget + 토큰 카운터(B3)
- [`build/api-contract.md`](build/api-contract.md) — Memory facade / Store ABC / Embedder·LLM / MCP 도구 시그니처
- [`build/eval-and-reliability.md`](build/eval-and-reliability.md) — eval harness·골든셋·LLM mock + 실패모드·성능예산
- **[`security-spec.md`](security-spec.md)** — C2 purge invariant/crypto-shredding · H8 localhost CSRF/DNS-rebinding · H9 MCP 위협모델 · H13 키 lifecycle · H7 import sandbox
- 리스크 전체 + B1~B7 결정 → [`risks.md`](risks.md) (D21)
