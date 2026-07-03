# cold-memo: 제품 설계 문서 (v0.1)

> ⚠️ HISTORICAL analysis (2026-06, pre-SPEC, "cold-memo" working name) — superseded by SPEC.md + code (CLAUDE.md §1).

> 청사진(`docs/blueprint-combine-the-best.md`)을 실제 구현 가능한 설계로 구체화. 로컬 우선·단일 사용자로 시작, 프로덕션급 지향(추후 출시 여지).
> 결정 반영(2026-06-21): **단위 = atomic fact + 경량 edge**, **write = 하이브리드(파이프라인 추출 기본 + LLM self-edit 도구)**, **procedural memory v1 포함**.

---

## 1. 기술 선택 (로컬 우선의 핵심 통찰)

> **단일 SQLite 파일 하나가 SoT + BM25 + 벡터 + 그래프 edge를 전부 담을 수 있다.** 이게 로컬 우선 개인 메모리의 결정적 단순화다.

| 레이어 | 선택 | 근거 |
|---|---|---|
| 언어 | Python 3.11+ | 레퍼런스 7종 전부 Python, ML 생태계 |
| Source of Truth | **SQLite** (WAL 모드) | 단일 파일, 트랜잭션, 운영 0. Letta의 SQL-SoT 규율을 개인 스케일로 |
| 키워드(BM25) | **SQLite FTS5** (내장) | 별도 의존성 없이 진짜 BM25. mem0는 Qdrant-bm25 필요했지만 우리는 내장으로 해결 |
| 벡터 | **sqlite-vec** (기본) | *같은 SQLite 파일* 안에 벡터 인덱스. dual-write 드리프트 원천 차단(같은 트랜잭션). 추후 LanceDB/pgvector/Qdrant로 추상화 교체 |
| 그래프 edge | **SQL `edges` 테이블** | 멀티홉 필요 전까지 Neo4j 도입 안 함(운영비/주입위험 회피). 경량 인접 |
| 임베딩 | 추상화(`Embedder` ABC): OpenAI `text-embedding-3-small` 기본 / 로컬 BGE·sentence-transformers | 주권형(sovereign) 옵션. A-MEM `LLMController` 패턴 |
| LLM | 추상화(`LLM` ABC): OpenAI/Anthropic/Ollama | 추출·충돌·merge·gradient에 사용. provider 무관 |

**결과:** `~/.cold-memo/memory.db` 단일 파일에 notes + FTS5 + 벡터 + edges + 버전히스토리 + provenance + job 큐가 전부. 백업 = 파일 복사. 이게 "진짜 내 것"의 물리적 의미.

### 1.1 저장 백엔드 전략 (결정 2026-06-21)

**SQLite 먼저 → 출시 시 Postgres+pgvector 어댑터 추가.** 되돌릴 수 없는 갈림길이 아님 — `store/`가 추상화돼 있어 앱 로직(RRF/dedup/충돌/budget/망각)은 백엔드 무관, 바뀌는 건 어댑터 3개(벡터·FTS·SQL 방언)뿐.

- 현재 실제 용도 = 로컬 개인용 → SQLite가 *더* 적합(서버 0, FTS5 BM25 내장, sqlite-vec brute-force가 개인 스케일 수천~수만에서 정확·충분).
- 제품 백엔드 = Postgres+pgvector(HNSW 스케일). 단 **진짜 BM25는 `pg_search`/VectorChord-bm25 확장 필요**(core `ts_rank`는 BM25 아님). Letta 선례와 동일(Postgres 메인 + SQLite dev).

**이식성 규칙(지금 지킬 것):**
1. 방언/엔진 특화 기능은 전부 `store/` 어댑터 뒤로 격리 — 코어/write/read/forget는 `Store` 인터페이스만 호출.
2. SQL은 표준 위주, SQLite-전용 관용구(예: `INSERT OR REPLACE`) 금지 → upsert는 어댑터 메서드로.
3. JSON 컬럼은 list/dict를 TEXT(json)로 직렬화하는 헬퍼로 통일(SQLite엔 native JSON 타입 없음, PG는 jsonb — 어댑터가 흡수).
4. 벡터/FTS 인터페이스: `Store.knn(emb, k, filter)` / `Store.bm25(query, k, filter)`만 노출 → sqlite-vec↔pgvector, FTS5↔pg_search 교체가 어댑터 교체로 끝남.
5. 타임스탬프는 ISO8601 UTC 문자열로 통일(SQLite TEXT ↔ PG timestamptz 양쪽 안전).

### 1.2 패키징 & 레이어 분리 (결정 2026-06-21)

> **로컬 설치는 "프로그램처럼" 간단해야 하고, 서비스/무거운 백엔드는 코어와 분리(decouple)된다.**

두 레이어, **하드 의존성 경계**:

| 레이어 | 패키지 | 내용 | 의존성 |
|---|---|---|---|
| **Core (로컬)** | `cold-memo` | 엔진 + **SQLite 백엔드** + 라이브러리 API + CLI. 인프로세스. | 최소(`pydantic`, `numpy`). 서버/Postgres 코드 **import 금지** |
| **Server (제품)** | `cold-memo[server]` → 추후 별도 `cold-memo-server` | FastAPI + **Postgres+pgvector** 백엔드 + auth/멀티테넌트 | 무거움(`fastapi`,`psycopg`,`pgvector`). 로컬 설치엔 미포함 |

**설치 UX (로컬):**
```
pip install cold-memo            # 끝. 서버 0, API 키 0(아래 오프라인 임베더 기본)
  → from cold_memo import Memory  # 라이브러리
  → cold-memo add "..." / search  # CLI
  → ~/.cold-memo/memory.db        # 단일 파일
pip install cold-memo[openai]    # 클라우드 임베딩/LLM 옵션
pip install cold-memo[local-llm] # 완전 오프라인(sentence-transformers/Ollama)
pip install cold-memo[server]    # 서비스(Postgres+FastAPI) — 분리된 무거운 레이어
```

**경계 규칙:**
1. `cold_memo` 코어는 `fastapi`/`psycopg`를 **절대 import 안 함**. Postgres 어댑터는 server 레이어에만 존재.
2. **오프라인 우선**: 외부 API 키/네트워크 없이도 동작해야 함 → 기본 임베더는 의존성 0의 결정적 `HashEmbedder`(데모/테스트용), 실제 품질은 `[openai]`/`[local-llm]` 옵션으로 업그레이드. 즉 "설치하면 바로 돈다".
3. **네이티브 확장 비의존**: 벡터는 기본적으로 BLOB + numpy brute-force KNN(개인 스케일 정확·충분, sqlite-vec 같은 로드 가능 확장의 플랫폼 이슈 회피). `[vec]` 옵션으로 sqlite-vec 가속.
4. 데이터(`~/.cold-memo/memory.db`)는 프로그램과 분리 — 재설치/업그레이드해도 데이터 보존, 백업=파일 복사.

### 1.3 배포(distribution) 전략 (결정 2026-06-21) — Docker 안 씀(로컬)

Docker는 *서버 환경 재현*용이지 *로컬 앱 설치 간편화*용이 아님 → 로컬 코어엔 부적합. 대상별:

| 대상 | 방식 |
|---|---|
| 개발자/파이썬 | **uv** 주력(`uv tool install cold-memo` / 무설치 `uvx cold-memo`), pipx/pip 폴백. `[project.scripts]` 엔트리포인트가 셋 다 지원 |
| 비개발자(파이썬 無) | **PyApp/uv 단일 바이너리** 또는 Nuitka — 파이썬 없이 실행 |
| 네이티브 앱 느낌 | **Homebrew tap** / Scoop / winget |
| **서버 레이어(분리됨)** | 여기서만 컨테이너 — **Podman**(데몬리스) 또는 Docker, 또는 Nix |

즉 로컬 = uv/PyPI, 컨테이너는 분리된 서버 레이어에 한정. `pyproject.toml`은 이미 이 경로를 지원(엔트리포인트 + extras).

### 1.4 완전 로컬 + 메모리 뷰어 (결정 2026-06-21)

> "이게 로컬에서 DB까지 논스톱으로 되고, 설치하면 기록이 어떻게 쌓였는지 보는 CLI/프로그램 화면이 있어야 한다."

- **전부 로컬·논스톱**: 임베디드 DB(SQLite) + 오프라인 기본 임베더(`HashEmbedder`) → **네트워크/API 키 0으로도 끝까지 동작**. 외부 서비스 의존 없음. "로컬에서 자유롭게"의 보장.
- **기록 인스펙션(필수 기능)** — 설치 후 *내 기억이 어떻게 쌓였는지* 보는 수단. 두 층:
  1. **CLI 뷰어** (P1+): `list`(최근/필터), `show <id>`(노트 + provenance + 버전 히스토리 + edge), `search`, `stats`(타입/상태별 카운트, 총량), `timeline`(시계열). 보기 좋은 테이블 출력.
  2. **프로그램 화면** (P3~, "program screen") — 옵션 분리. 후보: (a) **로컬 웹 UI** `cold-memo ui` → `localhost:PORT` 브라우저 대시보드(검색·edge 그래프·버전·decay 점수 시각화), (b) **TUI**(터미널 풀스크린 브라우저). 둘 다 *로컬 단일유저 인스펙션*용.
- ⚠️ **구분**: 이 "로컬 뷰어 UI"(localhost, 단일 .db 위 읽기 중심)는 §1.2의 *멀티테넌트 제품 서버*와 **별개**. 로컬 뷰어 = 코어/경량 extra(`cold-memo[ui]`), 제품 서버(FastAPI+Postgres) = 분리된 무거운 레이어.
- 언어 결정(진행 중)과 연동: "프로그램 화면" 형태(TUI / 로컬 웹 / 데스크톱 Tauri)가 메인 언어 선택에 일부 좌우됨 → 언어 종합에 반영.

---

## 2. 데이터 모델

### 2.1 Note (Pydantic, content만 임베딩)

```python
class Scope(BaseModel):
    user_id: str = "default"
    agent_id: str | None = None
    session_id: str | None = None

class Source(BaseModel):           # provenance 1급
    kind: Literal["message","document","tool","manual"]
    ref: str                        # msg id / 파일 경로 등
    role: str | None = None
    content_hash: str               # 원천 dedup/감사
    observed_at: datetime           # 사건 시각(상대시간 grounding 기준)

class Note(BaseModel):
    id: str                                     # uuid4
    content: str                                # 유일하게 임베딩되는 필드 (atomic fact)
    memory_type: Literal["semantic","episodic","procedural"]
    # 자기서술 메타 (A-MEM 개념 + MemOS 엄밀성)
    keywords: list[str] = []
    tags: list[str] = []
    context: str = ""                           # 한 줄 요약
    confidence: float = 1.0                     # 0..1
    # 스코프 & 프로비넌스
    scope: Scope
    sources: list[Source] = []
    # 라이프사이클 (MemOS status-as-filter + 버전)
    status: Literal["active","archived","deleted"] = "active"
    version: int = 1
    # bi-temporal (Graphiti): 트랜잭션축 + valid축
    created_at: datetime                        # 시스템 기록 시각
    expired_at: datetime | None = None          # 시스템상 더 이상 유효하지 않게 된 시각
    valid_at: datetime | None = None            # 사실이 참이 된 시각
    invalid_at: datetime | None = None          # 사실이 거짓이 된 시각
    # 망각/랭킹 신호
    importance: float = 0.5
    last_accessed: datetime | None = None
    access_count: int = 0
    decay_S: float = 1.0                        # MemoryBank R=e^(−Δt/S), 회상마다 증가
    # embedding은 Note에 인라인하지 않음 → note_vec 테이블 (MemOS의 인라인 임베딩 안티패턴 회피)
```

> **A-MEM 대비 교정:** `links`를 노트 필드에 두지 않고 **실제 `edges` 테이블로 materialize**. `evolution_history`/`retrieval_count` 같은 "선언만 하고 안 쓰는 죽은 필드" 금지 — 쓰는 필드만.

### 2.2 Edge (경량 그래프, SQL)

```python
class Edge(BaseModel):
    src_id: str
    dst_id: str
    relation: str                # snake_case: "supersedes","relates_to","mentions","caused_by"
    weight: float = 1.0
    created_at: datetime
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
```

### 2.3 SQLite 스키마 (DDL 스케치)

```sql
CREATE TABLE notes (
  id TEXT PRIMARY KEY, content TEXT NOT NULL, memory_type TEXT NOT NULL,
  keywords TEXT, tags TEXT, context TEXT, confidence REAL,            -- json list는 TEXT
  user_id TEXT NOT NULL, agent_id TEXT, session_id TEXT,
  status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, expired_at TEXT, valid_at TEXT, invalid_at TEXT,
  importance REAL DEFAULT 0.5, last_accessed TEXT, access_count INTEGER DEFAULT 0, decay_S REAL DEFAULT 1.0
);
CREATE INDEX idx_notes_scope  ON notes(user_id, agent_id, session_id, status);
CREATE INDEX idx_notes_type   ON notes(memory_type, status);

-- BM25 (FTS5): content + keywords + tags 색인
CREATE VIRTUAL TABLE note_fts USING fts5(content, keywords, tags, content='notes', content_rowid='rowid');

-- 벡터 (sqlite-vec): note id ↔ embedding, 동일 트랜잭션 dual-write
-- ^ SUPERSEDED: the hardcoded FLOAT[1536] violates CLAUDE.md I8 — the shipped vec0 dim is
--   Embedder.meta.dim (256 for the default HashEmbedder), written at migrate time, never a literal.
CREATE VIRTUAL TABLE note_vec USING vec0(note_id TEXT PRIMARY KEY, embedding FLOAT[{meta.dim}]);

CREATE TABLE edges (
  src_id TEXT, dst_id TEXT, relation TEXT, weight REAL DEFAULT 1.0,
  created_at TEXT, valid_at TEXT, invalid_at TEXT,
  PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX idx_edges_src ON edges(src_id);  CREATE INDEX idx_edges_dst ON edges(dst_id);

CREATE TABLE note_history (                    -- 버전 스냅샷 (Letta block_history + MemOS ArchivedTextualMemory)
  id TEXT, version INTEGER, snapshot TEXT, update_type TEXT, changed_at TEXT,  -- update_type: conflict|dedup|extract|feedback|manual
  PRIMARY KEY (id, version)
);

CREATE TABLE sources (note_id TEXT, kind TEXT, ref TEXT, role TEXT, content_hash TEXT, observed_at TEXT);
CREATE INDEX idx_sources_note ON sources(note_id);  CREATE INDEX idx_sources_hash ON sources(content_hash);

CREATE TABLE jobs (                            -- durable 백그라운드 큐 (fire-and-forget landmine 회피)
  id TEXT PRIMARY KEY, kind TEXT, payload TEXT, status TEXT, attempts INTEGER DEFAULT 0,
  run_after TEXT, created_at TEXT, locked_at TEXT
);
```

---

## 3. API 표면 (`Memory` 파사드)

```python
class Memory:
    # --- 쓰기 ---
    async def add(self, messages: list[Msg] | str, *, scope: Scope, infer: bool = True,
                  observed_at: datetime | None = None) -> list[Note]: ...     # 파이프라인 추출
    # self-edit 경로(에이전트가 직접 호출하는 도구) — 같은 WriteCore로 수렴
    def memory_tools(self, scope: Scope) -> list[Tool]: ...                   # append/replace/insert/delete

    # --- 읽기 ---
    async def search(self, query: str, *, scope: Scope, k: int = 10,
                     token_budget: int | None = None, as_of: datetime | None = None,
                     include_archived: bool = False) -> SearchResult: ...

    # --- 명시적 편집 ---
    async def get(self, id) ; async def update(self, id, **f) ; async def delete(self, id)

    # --- 유지보수(백그라운드 or 수동 트리거) ---
    async def consolidate(self, scope: Scope) -> None    # decay+merge+archive
    # --- procedural ---
    async def optimize_prompt(self, name: str, trajectory, feedback) -> str
    def get_procedural(self, name: str) -> str           # 현재 행동 지침/시스템프롬프트 조각
```

> 기본은 `add()`(파이프라인). 에이전트형 사용엔 `memory_tools()`로 self-edit 도구 노출 — **두 경로 모두 동일한 `WriteCore`(dedup→conflict→persist)로 수렴**해 일관성 보장(Letta는 두 경로가 갈라짐).

---

## 4. Write path (하이브리드)

```
add(messages)  ──┐                         memory_tools (self-edit) ──┐
                 ▼                                                     ▼
        [EXTRACT (파이프라인 전용)]                              (도구가 직접 노트 후보 제시)
        mem0 단일콜 additive 프롬프트                                   │
        + UUID→int remap(환각방지)                                     │
        + Observed-Date grounding                                      │
        + Graphiti anti-generalization                                 │
        + memory_type 분류(MemOS)                                      │
                 └───────────────┬─────────────────────────────────────┘
                                 ▼   ★ 공통 WriteCore
        [DEDUP]  uuid5(normalized) exact → 엔트로피게이트+MinHash(0.9)
                 → 의미 near-dup(코사인 임계) → 모호분만 배치 LLM
        [CONFLICT]  같은 endpoint/주제 후보 검색 → dual-candidate 프롬프트
                 (중복 vs 모순 한 콜) → 결정적 freshness(valid_at 비교, LLM 금지)
                 → 모순이면 기존 노트 status=archived + invalid_at, supersedes edge 생성
        [PERSIST]  단일 트랜잭션: notes + note_fts + note_vec(동일 id) + edges
                 + sources(provenance) + note_history(스냅샷)
```

핵심 규율:
- **결정적 freshness**: "무엇이 최신인가"는 `valid_at`/`observed_at` 비교로 결정. LLM은 *후보 추출/모순 판정*만, 최신성 *결정*은 코드.
- **비파괴**: 모순 노트는 삭제 X → `status=archived` + `invalid_at` + `supersedes` edge. 감사·시점 쿼리 보존.
- **async + durable**: 추출/충돌은 무거우니 `jobs` 큐로 비동기 처리(개인 로컬에선 인프로세스 워커, 재시작 안전).

---

## 5. Read path

```
search(query, scope, k, token_budget, as_of)
 1. FILTER     scope(user/agent/session) + 기본 status='active'
               + temporal: as_of 주면 valid_at<=as_of<invalid_at, 기본 만료 제외  ← Graphiti footgun 회피
 2. FAN-OUT    병렬 3신호:
               · semantic  = note_vec KNN (sqlite-vec)
               · bm25      = note_fts MATCH (FTS5), 쿼리길이 적응형 시그모이드 정규화(mem0 scoring.py:16-54)
               · entity/edge = 쿼리 엔티티 → edge 1-hop boost, promiscuity 다운웨이트 1/(1+0.001(n-1)²)
 3. FUSE       RRF (파라미터 프리, 전역-divisor footgun 회피)
 4. RERANK     (옵션) cross-encoder: self-host=BGE / API-only=LLM-boolean+logprobs
               + recency/scope 메타 boost score*=(1+w) clamp[0,1]
 5. ★ BUDGET   token-budget 패커: 랭킹 상위를 token_budget에 맞춰 채움
               (7종 전부 미구현 → 우리가 소유). 부분 노트 truncation 정책 포함.
 6. SIDE-FX    선택 노트 access_count++, last_accessed=now, decay_S++ (회상 강화)
```

---

## 6. 충돌/시간 모델 (bi-temporal)

- 4 타임스탬프: `created_at`/`expired_at`(트랜잭션축), `valid_at`/`invalid_at`(valid축). + source `observed_at`.
- 시점 쿼리: `search(as_of=T)` → "T 시점에 우리가 알던/참이던 것".
- 모순 처리(결정적): 새 사실의 `valid_at`이 기존보다 **이후**일 때만 기존을 archive + `invalid_at=new.valid_at`. 더 오래된 정보가 들어오면 *새 노트*를 무효 처리(Graphiti `edge_operations.py:826-839` 규칙).
- dual-candidate 프롬프트: 중복후보 + 모순후보를 연속 인덱스로 한 콜에 판정(`dedupe_edges.py:43-100` 이식).

---

## 7. 망각 / consolidation 엔진 (우리가 소유 — cold-memo 핵심)

백그라운드 `consolidate()` (debounce, `jobs` 큐):

1. **decay 스코어**: `score = w_r·recency + w_i·importance + w_rel·relevance`
   - recency = `e^(−Δt / decay_S)` (MemoryBank), 회상 시 `decay_S++`로 강화.
   - importance: 추출 시 LLM 추정 + feedback EMA(`new = prev + 0.1·(rating−prev)`, Cognee).
2. **consolidation**: 같은 주제 episodic 클러스터 → 시맨틱 요약 노트 생성, 원본은 cold 강등(요약 노트가 supersede). Letta sleep-time 개념.
3. **soft-archive**: 스코어 < 임계 또는 capacity 캡 초과 → `status='archived'`(삭제 X, 읽기에서 자동 제외 — MemOS status-as-filter).
4. **capacity 캡**: type별(예: episodic 활성 N개) 초과 시 최저 스코어부터 강등(MemOS, 단 결정적).

> 이 4개가 어디서도 제대로 구현 안 된 부분. cold-memo의 차별점.

---

## 8. Procedural memory (v1 포함)

행동(=어떻게 행동할지) 메모리. `memory_type="procedural"` 노트로 저장하되 전용 최적화 경로:

- `optimize_prompt(name, trajectory, feedback)` → **2단계 gradient**(LangMem):
  1. diagnose: think/critique/recommend 루프 → 실패 근거 없으면 `warrants_adjustment=False`(무변경 게이트로 프롬프트 드리프트 방지).
  2. edit: 별도 제약 리라이트 + **var-healer**(f-string 변수 mask→edit→unmask, 누락 시 hard-fail).
- 저장/버전: 일반 노트와 동일 테이블/버전 히스토리 재사용 → 회귀 시 롤백 가능.
- `get_procedural(name)`로 현재 시스템프롬프트 조각을 에이전트에 주입.

---

## 9. 디렉터리 구조

```
cold_memo/
  __init__.py
  models.py            # Note, Edge, Scope, Source, SearchResult (Pydantic)
  api.py               # Memory 파사드
  store/
    sqlite.py          # 연결/트랜잭션/마이그레이션 (단일 .db)
    notes.py           # notes CRUD + note_history
    vectors.py         # sqlite-vec (Embedder ABC)
    fts.py             # FTS5 BM25
    edges.py           # 경량 그래프
  write/
    extract.py         # 파이프라인 추출 (프롬프트 + UUID→int remap)
    dedup.py           # uuid5 → entropy+MinHash → 의미 near-dup → LLM
    conflict.py        # bi-temporal + dual-candidate + 결정적 freshness
    core.py            # WriteCore (양 경로 수렴), jobs 큐 producer
    tools.py           # self-edit 도구 (append/replace/insert/delete)
  read/
    retrieve.py        # 병렬 하이브리드 fan-out
    fuse.py            # RRF + 메타 boost
    rerank.py          # BGE / LLM-boolean (옵션)
    budget.py          # ★ token-budget 패커
  forget/
    decay.py           # 스코어
    consolidate.py     # 클러스터/요약/archive
    worker.py          # durable jobs 워커 (debounce, 재시도)
  procedural/
    optimize.py        # 2단계 gradient + var-healer
  llm/
    base.py            # LLM ABC + Embedder ABC
    providers.py       # openai/anthropic/ollama
  prompts/             # extraction / dedupe / merge / gradient / conflict
  eval/
    harness.py         # 메트릭 + LLM mock
    datasets/          # 합성 + LoCoMo/LongMemEval 어댑터
```

---

## 10. Eval harness (공개 벤치가 안 재는 것 — 처음부터)

| 측정 | 방법 |
|---|---|
| 추출 품질 | 골든 (대화→기대 사실) 셋, precision/recall |
| dedup 정확도 | 의미 중복쌍 셋, 병합 정확/오병합률 |
| **freshness/충돌** | 시계열 모순 시나리오("이직했다") → 올바른 archive/현재값 반환 |
| **forgetting** | 캡/decay 후 보존돼야 할 것 vs 강등돼야 할 것 |
| retrieval precision@k | 쿼리→기대 노트 |
| cross-scope 누출 | 타 user/session 노트 반환=0 검증 |
| 보조 | LoCoMo/LongMemEval 어댑터 |

**철칙:** 코어(dedup/conflict/decay)는 **LLM mock**으로 결정적 단위테스트(A-MEM·langmem이 안 해서 망한 부분).

---

## 11. 단계별 구현 계획

| Phase | 산출물 | 핵심 |
|---|---|---|
| **P1 골격** | SQLite 단일파일 store + models + `add(infer)` 추출 + `search` 하이브리드 + RRF | 한 파일에 SoT+FTS5+vec. eval harness 동시 시작 |
| **P2 정확성** | dedup(티어드) + bi-temporal conflict + 결정적 freshness + provenance/버전 | "메모리 두뇌" 핵심 1 |
| **P3 읽기 품질** | token-budget 패커 + rerank(옵션) + 메타 boost | 우리가 소유하는 칸 |
| **P4 망각** | decay + consolidation + durable 워커 + capacity 캡 | cold-memo 차별점 |
| **P5 procedural** | gradient 최적화 + var-healer | 행동 메모리 |
| **P6 agentic write** | self-edit 도구(공통 WriteCore) | 하이브리드 완성 |

> P1·P2만으로도 "ADD-only 누적"을 넘는, mem0/Letta보다 충돌해결이 제대로 되는 개인 메모리가 나온다. P3·P4가 진짜 차별점(아무도 안 한 token budget + 망각).

---

## 13. Claude Code 연동 (MCP) — 검증됨 2026-06-21

cold-memo의 기억을 Claude Code 세션에서 검색/요약하는 표준 경로 = **MCP 서버**. (출처: code.claude.com/docs — mcp, mcp-quickstart, hooks-guide, memory)

- **로컬(우리 기본): stdio MCP 서버 → OAuth 불필요.** Claude Code가 `python -m cold_memo.mcp`를 서브프로세스로 띄워 stdin/stdout 통신. 등록: `claude mcp add cold-memo -- python -m cold_memo.mcp` 또는 `.mcp.json`. → "전부 로컬·논스톱"과 정합(인증 0).
- **원격(제품화 후): HTTP MCP 서버 → OAuth 2.0/2.1 완전 지원**(dynamic client registration / 사전 credential / static bearer token). `claude mcp add --transport http https://.../mcp` + 세션 내 `/mcp` 브라우저 인증. 토큰은 OS 키체인 저장·자동 갱신.
- **노출 도구**: `search_memory(query)`, `add_memory(fact)`, `summarize(topic)`, `list`/`timeline`. 모델이 필요 시 tool-call로 호출.
- **컨텍스트 주입**: 기본은 tool-call 기반(자동 주입 아님). 세션 시작 자동 주입은 (a) **SessionStart 훅**(스크립트가 cold-memo 질의 → 컨텍스트 반환), (b) **MCP 리소스**(`@cold-memo:...` @mention).
- **네이티브 Claude Code 메모리와 상보적**: CLAUDE.md/auto-memory/session-memory는 프로젝트 운영 컨텍스트, cold-memo는 크로스-프로젝트 도메인 사실/기억(SQLite). 중복 아님.
- **언어 정합**: Python은 MCP SDK first-class → MCP 서버 구현 용이(D10 보강). MCP 서버는 코어를 호출하는 얇은 어댑터(`cold_memo.mcp`)로, 코어/서버 분리(§1.2)와 일관.

> ⚠️ **OAuth 답**: 로컬 통합엔 OAuth 불필요(stdio). OAuth는 원격 호스팅 시에만 등장하며 그땐 완전 지원.

## 14. 다음 액션 (현재 = 기획 모드, 코드 보류)
> 사용자 지시(2026-06-21): 기획이 끝날 때까지 **코드 0**. 아래는 기획 완료 후 P1 범위.
- P1 코어: `cold_memo/` (models, store/sqlite, write, read, api, cli) + 최소 MCP 서버(`cold_memo.mcp`) + eval harness 시작.
- 의존성 확정: 코어=`pydantic`+`numpy`; 옵션=`[openai]`/`[local-llm]`/`[vec]`/`[server]`.
- 기획에서 더 닫을 것: 노트 입자 세부, 프로그램 화면 형태(web/TUI), eval 케이스 설계.
