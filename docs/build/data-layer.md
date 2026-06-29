# DATA LAYER — SQLite DDL, event log, concurrency, migrations, re-embedding

> where_it_goes: Rewrite SPEC §2 (data model / DDL pointer) and §3 (storage layer) to point at a new focused doc `docs/data-layer.md` (this spec). Replace the DDL sketch in `analysis/design.md §2.3` with a pointer to this doc. Update SPEC §3 D17 wording per §2.2 recommendation below.

## cold-frame — Data Layer Spec (build-ready)

이 문서가 `analysis/design.md §2.3`의 DDL 스케치를 대체한다. 코딩은 이 문서대로 하면 mechanical하다.

핵심 설계 결정 (TL;DR):
- **notes 테이블 = source of truth (SoT)**, in-place 변경. "materialized view of append-only event log" 표현(SPEC §3 / D17)은 **폐기**하고 **co-written audit/sync event log**로 재명명. notes는 SoT, `events`는 같은 트랜잭션에서 함께 쓰이는 append-only 파생 로그. (감사 finding #1 해소)
- **v1에서 events 테이블은 만들되(스키마+co-write), 재구성/replay 로직은 만들지 않는다.** export = log dump, sync replay = v2. (정직한 권고: §2.3)
- 벡터 차원은 임베더 속성으로 DB 메타에 저장. 기본 = numpy-KNN over BLOB (차원 무관). `[vec]`(sqlite-vec)은 차원 고정 가상테이블이라 옵션 가속 경로로만, 임베더별 namespace. (감사 finding #4 해소)
- secret hard-purge는 append-only 불변을 명시적으로 깨는 carve-out: events 포함 전 store를 scrub. (감사 finding #2 해소)

---

### 1. 전체 DDL (canonical)

모든 타임스탬프 = ISO8601 UTC TEXT (`2026-06-21T08:30:00Z`). JSON list/dict = TEXT(json), 어댑터 헬퍼 `_dumps/_loads`로 통일. `INSERT OR REPLACE` 금지(어댑터 upsert 메서드).

```sql
-- ============ PRAGMAs (연결마다, 아래 §3) ============
-- journal_mode=WAL; busy_timeout=5000; foreign_keys=ON;
-- synchronous=NORMAL; wal_autocheckpoint=1000

-- ============ meta: 단일행 key/value 구성·버전 ============
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- 필수 키(첫 init 시 기록):
--   schema_version        INTEGER (마이그레이션, §4)
--   device_id             TEXT  (uuid4, 이 .db 파일 고유; HLC/event에 박힘)
--   hlc_last              TEXT  (마지막 발급 HLC, "<millis>:<counter>:<device_id>")
--   embedder_id           TEXT  (예 "hash-v1" / "openai:text-embedding-3-small")
--   embedder_dim          INTEGER (예 256 / 1536)
--   embedder_metric       TEXT  ("cosine")
--   vec_backend           TEXT  ("numpy" | "sqlite-vec")
--   created_at            TEXT

-- ============ notes: SoT, in-place 변경 ============
CREATE TABLE notes (
  id            TEXT PRIMARY KEY,            -- uuid4 (외부 노출 stable id)
  content       TEXT NOT NULL,
  memory_type   TEXT NOT NULL,               -- semantic|episodic|procedural
  keywords      TEXT NOT NULL DEFAULT '[]',  -- json list
  tags          TEXT NOT NULL DEFAULT '[]',  -- json list
  context       TEXT NOT NULL DEFAULT '',
  confidence    REAL NOT NULL DEFAULT 1.0,   -- 추출 확신 (≠importance)
  importance    REAL NOT NULL DEFAULT 0.5,   -- 장기 가치
  -- scope
  user_id       TEXT NOT NULL DEFAULT 'default',
  agent_id      TEXT,
  session_id    TEXT,
  -- lifecycle
  status        TEXT NOT NULL DEFAULT 'active',  -- active|archived|deleted|pending
  version       INTEGER NOT NULL DEFAULT 1,
  held_for_human INTEGER NOT NULL DEFAULT 0,     -- 0/1 (Triage)
  triage_reason  TEXT,                            -- conflict|ambiguous_merge|low_confidence|pin_adjacent|null
  pinned         INTEGER NOT NULL DEFAULT 0,      -- 0/1 (decay/archive 면제)
  redaction      TEXT,                            -- null | 'pii' | 'secret_tombstone'
  -- bi-temporal (R1): created/expired = transaction축, valid/invalid = valid축
  created_at    TEXT NOT NULL,
  expired_at    TEXT,
  valid_at      TEXT,
  invalid_at    TEXT,
  -- forgetting 신호 (R5)
  last_accessed TEXT,
  access_count  INTEGER NOT NULL DEFAULT 0,
  decay_S       REAL NOT NULL DEFAULT 1.0,
  content_hash  TEXT NOT NULL,               -- sha256(normalized content) — dedup/event grain
  embedder_id   TEXT NOT NULL                -- 이 row의 벡터를 만든 임베더 (re-embed 추적, §5)
);
CREATE INDEX idx_notes_scope  ON notes(user_id, agent_id, session_id, status);
CREATE INDEX idx_notes_type   ON notes(memory_type, status);
CREATE INDEX idx_notes_valid  ON notes(valid_at, invalid_at);   -- as-of 쿼리
CREATE INDEX idx_notes_triage ON notes(held_for_human) WHERE held_for_human = 1;
CREATE INDEX idx_notes_hash   ON notes(content_hash);
CREATE INDEX idx_notes_embedder ON notes(embedder_id) WHERE status = 'active';  -- re-embed 스캔

-- ============ note_fts: FTS5 (external content over notes) ============
CREATE VIRTUAL TABLE note_fts USING fts5(
  content, keywords, tags,
  content='notes', content_rowid='rowid'
);
-- external content FTS5는 자동 동기화 안 됨 → notes write마다 어댑터가 명시 동기화(§2 트랜잭션).
-- 검색은 status='active' 필터를 SQL JOIN으로(FTS는 status 모름).

-- ============ note_vec: 임베딩. 기본 BLOB(numpy), [vec]는 별도 ============
-- 기본(vec_backend='numpy'): 일반 테이블, 차원 무관.
CREATE TABLE note_vec (
  note_id     TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
  embedder_id TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  embedding   BLOB NOT NULL          -- float32 little-endian, dim*4 bytes
);
CREATE INDEX idx_vec_embedder ON note_vec(embedder_id);
-- [vec] 가속 경로(vec_backend='sqlite-vec', 옵션): 위 테이블과 공존.
--   가상테이블 이름 = note_vec_<embedder_id sanitized>, 차원은 meta.embedder_dim로 런타임 생성:
--   CREATE VIRTUAL TABLE note_vec_<id> USING vec0(note_id TEXT PK, embedding FLOAT[<dim>]);
--   임베더가 바뀌면 새 차원 = 새 가상테이블(기존 것은 stale로 남겨두고 §5 마이그레이션이 정리).
--   ⇒ 1536 하드코딩 금지. 차원은 항상 meta에서 읽는다.

-- ============ edges ============
CREATE TABLE edges (
  src_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  dst_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  relation   TEXT NOT NULL,           -- supersedes|relates_to|mentions|derived_from|caused_by
  weight     REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL,
  valid_at   TEXT,
  invalid_at TEXT,
  PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_dst ON edges(dst_id);

-- ============ note_history: 버전 스냅샷 ============
CREATE TABLE note_history (
  id          TEXT NOT NULL,
  version     INTEGER NOT NULL,
  snapshot    TEXT NOT NULL,          -- json: 그 버전의 notes row 전체
  update_type TEXT NOT NULL,          -- extract|dedup|conflict|feedback|manual|correct|consolidate
  changed_at  TEXT NOT NULL,
  PRIMARY KEY (id, version)
);

-- ============ sources: provenance (D-T4 invariant) ============
CREATE TABLE sources (
  note_id      TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,         -- message|document|tool|manual
  ref          TEXT NOT NULL,
  role         TEXT,
  content_hash TEXT NOT NULL,
  extractor    TEXT NOT NULL,         -- "pipeline:v1" / "tool:create_fact" / "manual"
  extracted_at TEXT NOT NULL,
  observed_at  TEXT NOT NULL          -- 상대시간 grounding 기준
);
CREATE INDEX idx_sources_note ON sources(note_id);
CREATE INDEX idx_sources_hash ON sources(content_hash);
-- provenance invariant(D-T4): 어떤 note도 status IN ('active') AND confidence>=0.4 이려면
--   sources 행이 ≥1 있어야 함. DB 트리거로 강제(아래 §1.1).

-- ============ access_log: REINFORCE 이력 (망각곡선) ============
CREATE TABLE access_log (
  note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  kind    TEXT NOT NULL DEFAULT 'search'  -- search|show|mcp
);
CREATE INDEX idx_access_note_ts ON access_log(note_id, ts);
-- retention/compaction(R5 무한성장 방지, 아래 §1.2): note당 최근 50행 cap + 90일 초과 다운샘플.

-- ============ events: co-written append-only audit/sync log (D17/D-S3) ============
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 로컬 단조 순서
  event_id    TEXT NOT NULL UNIQUE,               -- uuid4 (멱등 키)
  device_id   TEXT NOT NULL,
  hlc         TEXT NOT NULL,                       -- "<millis>:<counter>:<device_id>"
  entity      TEXT NOT NULL,                       -- 'note' | 'edge'
  entity_id   TEXT NOT NULL,                       -- note.id 또는 "src|rel|dst"
  op          TEXT NOT NULL,                       -- create|update|archive|delete|purge
  content_hash TEXT,                               -- note면 새 content_hash, 아니면 null
  payload     TEXT NOT NULL,                       -- json: 변경 후 entity 스냅샷(또는 purge면 tombstone)
  ts          TEXT NOT NULL                        -- wall-clock(=created_at)
);
CREATE INDEX idx_events_entity ON events(entity, entity_id);
CREATE INDEX idx_events_hlc    ON events(hlc);
-- v1: append + export만. replay/머지 코드는 v2. (정직 권고 §2.3)

-- ============ jobs: durable 백그라운드 큐 ============
CREATE TABLE jobs (
  id          TEXT PRIMARY KEY,       -- uuid4
  kind        TEXT NOT NULL,          -- consolidate|reembed|conflict_llm|dedup_llm
  payload     TEXT NOT NULL,          -- json
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|dead
  attempts    INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  dedup_key   TEXT,                   -- debounce: 동일 키 pending 1개만
  run_after   TEXT NOT NULL,          -- ISO8601, 백오프 스케줄
  locked_by   TEXT,                   -- 워커 lease 토큰
  locked_at   TEXT,                   -- lease 시작(만료 판정)
  last_error  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX idx_jobs_claim ON jobs(status, run_after);
CREATE UNIQUE INDEX idx_jobs_dedup ON jobs(dedup_key) WHERE status = 'pending' AND dedup_key IS NOT NULL;
```

#### 1.1 Provenance invariant 트리거 (D-T4, finding #9)

quarantine = **status='pending'** (Status에 4번째 값 추가). §5 read FILTER 기본 = `status='active'`이므로 pending은 자동으로 검색 제외, Triage/`list --status pending`에서만 노출.

```sql
-- active+high-confidence인데 source 없으면 거부 (provenance invariant)
CREATE TRIGGER trg_provenance_active
BEFORE UPDATE OF status ON notes
WHEN NEW.status='active' AND NEW.confidence>=0.4
     AND NEW.redaction IS NOT 'secret_tombstone'
     AND (SELECT COUNT(*) FROM sources WHERE note_id=NEW.id)=0
BEGIN
  SELECT RAISE(ABORT, 'provenance invariant: active high-confidence note needs >=1 source');
END;
```
- WriteCore가 항상 sources를 notes와 같은 트랜잭션에서 먼저 insert하므로 정상 경로는 안 걸린다. self-edit/agentic write(§6, P6)가 source 누락 시 여기서 fail-fast → "두 write 경로 발산" 안티패턴 차단.
- confidence<0.4 또는 source 없는 LLM 사실 → status='pending'으로 들어오고 트리거 통과(active 아님). Triage에서 사람이 승격.

#### 1.2 access_log retention (R5, finding #3)

REINFORCE(§5 step 6)가 반환 노트마다 `INSERT INTO access_log(note_id, ts, kind)`. 무한성장 방지:
- **per-note cap**: insert 후 `DELETE FROM access_log WHERE note_id=? AND ts < (SELECT ts FROM access_log WHERE note_id=? ORDER BY ts DESC LIMIT 1 OFFSET 50)` — note당 최근 50행만.
- **age 다운샘플(consolidate 잡에서)**: 90일 초과 행은 일 1행으로 collapse (같은 날 여러 접근 → COUNT만 보존, 첫 ts 유지).
- 망각곡선 sparkline은 50 spike로 충분(ux §8.5). 없으면 현재 strength로 degrade.

---

### 2. 이벤트 로그 & current-row 관계 (D17, finding #1·#2)

#### 2.1 모델: notes = SoT, events = co-written 파생 로그

SPEC §3의 "current row = materialized view of append-only event log"는 **틀린 표현이다.** §4 PERSIST가 notes/fts/vec를 in-place 변경하고, FTS5/sqlite-vec는 이벤트 로그 위 SQL VIEW가 될 수 없다. 따라서:

> **재명명(SPEC §3 교체 문구):** "notes/edges가 source of truth이며, 모든 변경은 같은 트랜잭션에서 append-only `events` 로그에 co-write된다(D17). 이 로그가 export·backup·future-sync(D-S3/v2)의 단일 primitive다. current row는 events의 view가 아니라 별도 in-place 테이블이다."

co-write 규칙 (모든 WriteCore commit 내부):
```
WriteCore.commit(tx):
  1. notes upsert (+fts sync, +vec upsert, +sources, +note_history)   # SoT
  2. hlc = next_hlc()                                                  # §2.4
  3. events.insert(event_id=uuid4, device_id, hlc, entity, entity_id,
                   op, content_hash, payload=json(note_after), ts=now) # 파생 로그
  # 1~3 단일 트랜잭션. 실패 시 전체 롤백 → SoT와 로그 절대 발산 안 함.
```

#### 2.2 멱등성

- `event_id` UNIQUE → 동일 이벤트 재적용 무시(import/sync 멱등).
- v2 replay(여기선 미구현)는 `(entity, entity_id)`별 HLC 최대값만 SoT에 반영, content_hash로 no-op 판정.

#### 2.3 v1에 할 것 vs 미룰 것 (정직한 권고)

**v1 (P1~P2에 포함):**
- `events` 테이블 생성 + 모든 WriteCore commit에서 co-write.
- `device_id`/HLC 발급.
- `export`가 events.ndjson 덤프(§7 번들).

**v1에서 미룰 것 (defer to v2 sync ADR):**
- events → SoT replay/rebuild 로직.
- cross-device 머지/충돌(D-S4·S5).
- 로그 compaction/GC (D-S 보류 항목).

**근거(skeptical):** events를 SoT의 view로 만들면 §4 in-place mutation·FTS5·sqlite-vec과 정면충돌하고, replay 엔진은 v1에 쓸 곳이 없다(sync는 v2). 그러나 co-write 자체는 비용이 거의 없고(트랜잭션 내 INSERT 1개), 나중에 events 컬럼을 retrofit하는 게 진짜 고통이다 → **스키마+co-write는 지금, replay는 나중.** 이게 D-S3 "v1 미리 시행"의 합리적 해석이다.

#### 2.4 HLC (Hybrid Logical Clock) 발급

```python
def next_hlc(meta) -> str:
    # meta.hlc_last = "<millis>:<counter>:<device_id>"
    now_ms = int(time.time() * 1000)
    last_ms, last_c, _ = parse(meta["hlc_last"])
    if now_ms > last_ms:
        ms, c = now_ms, 0
    else:                       # clock 후퇴/동일 ms → counter 증가
        ms, c = last_ms, last_c + 1
    hlc = f"{ms}:{c}:{meta['device_id']}"
    meta["hlc_last"] = hlc      # 같은 트랜잭션에서 갱신
    return hlc
```
tie-break 순서(D-S5) = `(hlc_ms, hlc_counter, device_id, content_hash)`.

---

### 3. 동시성 (CLI + MCP + UI + worker, 1 .db)

SQLite WAL은 **다중 reader + 단일 writer**. 4 프로세스가 같은 `.db`를 열 수 있다.

#### 3.1 연결 PRAGMA (모든 연결, `SQLiteStore.connect()`)
```python
conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)  # autocommit; 명시 BEGIN
conn.execute("PRAGMA journal_mode=WAL")        # 영구(파일 속성), 첫 연결 1회면 충분하나 매번 안전
conn.execute("PRAGMA busy_timeout=5000")       # writer 경합 시 5s 재시도(SQLITE_BUSY 회피)
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA synchronous=NORMAL")      # WAL+NORMAL = 안전·빠름(개인 스케일)
conn.execute("PRAGMA wal_autocheckpoint=1000")
```

#### 3.2 쓰기 직렬화
- 모든 write는 `BEGIN IMMEDIATE` 트랜잭션으로 시작 → write lock 즉시 획득, deferred 업그레이드 데드락(SQLITE_BUSY_SNAPSHOT) 회피.
- `busy_timeout=5000`이 동시 writer를 자동 큐잉. 5s 초과 시 SQLITE_BUSY → 지수 백오프 3회 재시도 래퍼(`_retry_write`), 그래도 실패면 에러 전파.
- 단일 프로세스 내 멀티스레드(UI ASGI)는 **연결 풀 X, 스레드별 연결**(sqlite3 connection은 스레드 간 공유 금지). write는 단일 writer 스레드 또는 프로세스 전역 `threading.Lock`로 직렬화.

```python
def _retry_write(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e) or "busy" in str(e):
                time.sleep(0.05 * (2**i)); continue
            raise
    raise
```

#### 3.3 워커 lease/lock (jobs)
프로세스 advisory lock 불필요 — DB row lease로 충분.

```sql
-- claim (BEGIN IMMEDIATE 안에서):
UPDATE jobs SET status='running', locked_by=:worker, locked_at=:now, attempts=attempts+1
WHERE id = (
  SELECT id FROM jobs
  WHERE status='pending' AND run_after<=:now
  ORDER BY run_after LIMIT 1
)
RETURNING id, kind, payload;       -- SQLite 3.35+ RETURNING
```
- 트랜잭션이 lock을 잡으므로 두 워커가 같은 job claim 불가(SKIP LOCKED 불요 — SQLite는 단일 writer).
- **stuck lease 회수**: `status='running' AND locked_at < now-300s`인 job은 claim 쿼리 WHERE에 OR로 포함(크래시 워커 회복).
- 완료: `UPDATE jobs SET status='done', updated_at=now`. 실패: `attempts<max_attempts`면 `status='pending', run_after=now+backoff(attempts)`, 아니면 `status='dead'`.
- **debounce**: enqueue 시 `idx_jobs_dedup` UNIQUE가 동일 `dedup_key` pending 중복을 막음 → consolidate 폭주 방지.
- 워커는 인프로세스 단일 스레드 폴링(개인 로컬). CLI/MCP는 producer만(enqueue), `cold-frame ui`나 `cold-frame mcp`가 백그라운드 워커 1개 기동(중복 기동돼도 lease로 안전).

#### 3.4 reader 일관성
- search/show는 read-only(WAL이라 writer 안 막음). REINFORCE의 access_count++/access_log insert는 작은 write → `_retry_write`로 감싸고, 검색 응답을 막지 않게 best-effort(실패 시 로그만, 검색 결과는 이미 반환).

---

### 4. 마이그레이션 (live 개인 .db)

자동 init이지만 스키마 진화는 필요. ORM/Alembic 안 씀(코어 deps=pydantic+numpy).

- `meta.schema_version` 정수. 코드에 `MIGRATIONS: list[Migration]` (idx = 목표 버전), 각 = `(version:int, up:Callable[[conn], None])`.
- `Store.migrate()`가 첫 연결마다 호출: `current = meta.schema_version (없으면 0)`; `for m in MIGRATIONS[current:]: m.up(conn) within BEGIN IMMEDIATE; meta.schema_version = m.version`.
- 각 마이그레이션은 **멱등 가드**(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`는 PRAGMA table_info로 존재 확인 후).
- **백업 우선**: `migrate()`가 version 상승을 감지하면 실행 전 `memory.db` → `memory.db.bak.<version>` 파일 복사(checkpointed: `PRAGMA wal_checkpoint(TRUNCATE)` 후 복사). 실패 시 복원 안내.
- 파괴적 변경(컬럼 drop 등 SQLite 제약)은 `CREATE new + INSERT SELECT + DROP old + RENAME` 12-step 패턴, FK off 동안.
- FTS5/vec 재구축이 필요한 마이그레이션은 `INSERT INTO note_fts(note_fts) VALUES('rebuild')` / vec 테이블 재생성을 up()에 포함.

migration 0→1 = 초기 전체 스키마 생성(위 §1). 이후 finding 반영분(held_for_human, pending status, events, embedder_id 등)은 이미 v1 스키마에 포함되므로 별도 마이그레이션 불필요.

---

### 5. 임베딩 모델 스왑 → 재임베딩 (finding #4, deferred 항목)

차원·임베더가 row별로 다를 수 있다는 전제(`notes.embedder_id`, `note_vec.embedder_id/dim`).

**KNN 안전장치(혼합 차원 차단):** `Store.knn(emb, k, scope, statuses)`는 `meta.embedder_id`와 다른 embedder_id의 벡터를 **무조건 제외**(WHERE embedder_id=:current). 차원이 다른 BLOB을 numpy로 비교하면 garbage이므로 cross-embedder KNN 금지.

**스왑 트리거:** `cold-frame config --embedder X`가 새 임베더의 (id, dim)을 meta와 비교:
1. 동일 → no-op.
2. 다름 → meta.embedder_id/dim 갱신 + `enqueue job(kind='reembed', dedup_key='reembed')`.

**reembed 잡 (forget/worker, 백그라운드):**
```
for batch of notes WHERE status='active' AND embedder_id != meta.embedder_id (LIMIT 64):
    emb = new_embedder.embed([n.content for n in batch])
    in BEGIN IMMEDIATE:
      note_vec upsert (embedder_id=new, dim=new_dim, embedding=blob)
      notes.embedder_id = new   (각 row)
  re-enqueue self until 0 remaining
```
- 진행 중에는 stale embedder row가 KNN에서 제외되므로(위 안전장치) **검색이 garbage를 안 반환** — 대신 아직 재임베딩 안 된 노트는 일시적으로 semantic 검색에서 빠지고 BM25로만 잡힘(graceful degrade). 완료되면 전부 복귀.
- `[vec]` 백엔드면 새 차원 = 새 가상테이블 생성, 완료 후 옛 `note_vec_<oldid>` DROP.
- `cold-frame doctor`가 "N notes pending re-embed" 표시.

**v1 정직 권고:** 기본 HashEmbedder만 쓰는 사용자는 이 경로를 안 탄다. 스왑은 옵션 기능이라 P4(워커 존재) 이후 활성. P1~P3는 단일 임베더 가정으로 충분.

---

### 6. correct_memory / self-edit 경로 (finding #6·#8, P6)

WriteCore 단일 수렴(D8) — Store에 누락된 메서드 추가:

```python
# Store 인터페이스 추가분
def set_held_for_human(self, id: str, reason: str | None) -> None
def get_held(self, scope: Scope) -> list[Note]          # Triage 큐
def supersede(self, old_id: str, new: Note, emb) -> None # 옛→archived+invalid_at+supersedes edge+history, 단일 tx
```

`correct_memory(id, new_text)` = explicit-id supersede (similarity 우회):
```
ADMISSION(new_text)  # secret/PII 게이트 동일 적용 (§아래)
→ old = get(id)
→ new = Note(content=new_text, valid_at=now, sources=[old.sources + manual], ...)
→ WriteCore.supersede(old, new):  # §4 conflict의 commit 재사용
     old.status='archived'; old.invalid_at=now; history(old)
     insert new(active); edge(new -supersedes-> old)
     events.co-write x2 (archive old, create new)
```

self-edit 도구(P6): `create_fact / update_fact / supersede / forget`. **모두 ADMISSION→DEDUP→CONFLICT→PERSIST를 통과**(파이프라인과 동일 WriteCore). `--raw`/직접 agentic write도 ADMISSION(secret BLOCK)은 **우회 불가**(durability gate만 우회 가능, per-source 정책은 deferred). P6 acceptance "tool path passes dedup/conflict" = 이 도구들을 named 함수로 테스트.

---

### 7. Secret hard-purge 불변 (finding #2 — append-only carve-out)

D16/D-T5 hard-purge는 D17 append-only를 **명시적으로 깬다.** "purge invariant" = secret/PII 사실이 닿은 **모든** store를 물리 제거:

**닿을 수 있는 store(전수):** `notes`, `note_fts`(+FTS5 shadow tables `note_fts_data/_idx/_docsize/_content`), `note_vec`(+`[vec]` 가상테이블), `note_history`, `sources`, `access_log`, `events`(payload에 content), `jobs.payload`, export 번들.

**purge 알고리즘 (`forget(id, cascade=secret)`):**
```
in BEGIN IMMEDIATE:
  ids = {id} ∪ derived_from-cascade(id)
  for each i in ids:
    DELETE FROM note_vec/access_log/sources/note_history WHERE note_id=i  (또는 id)
    DELETE FROM notes WHERE id=i; (또는 → secret_tombstone row로 치환, content 비움)
    note_fts 동기 삭제 (external content: DELETE 트리거 or 'delete' 명령)
    DELETE FROM events WHERE entity_id=i           # ← append-only 예외(carve-out)
    UPDATE jobs SET payload=redact(payload) WHERE payload LIKE %i%
  남기는 것: tombstone row 1개 (notes: content='', redaction='secret_tombstone', status='deleted')
commit
PRAGMA wal_checkpoint(TRUNCATE)     # WAL 잔여 제거
PRAGMA secure_delete=ON; VACUUM     # free page overwrite
post-purge grep 검증: 원본 토큰이 .db/.db-wal/export에 0회 (SQLCipher면 이 단계 N/A)
```
- **HLC/sync 연속성:** purge는 해당 entity의 events를 제거하므로, 이미 sync한 디바이스에는 **purge-tombstone 이벤트**(op='purge', payload=tombstone)를 새로 발행 → 원격도 같은 entity_id 제거(v2). v1은 sync 없으므로 로컬 제거만.
- **BLOCK vs purge 관계(finding):** D-T3 BLOCK(pre-disk)이 정상 경로 — secret은 애초에 안 들어옴(tombstone만). D-T5 purge는 **late-detected secret/PII**(나중에 룰 업데이트로 발견) 전용 클린업 경로. 둘 다 명세에 공존하되 역할 분리.
- **SQLCipher(D-T6) 시:** 전체 DB가 AES-256이라 grep-verify 단계는 무의미(평문 잔여 없음) → 논리 삭제 + VACUUM만으로 충분, grep 단계 skip.

---

### 8. 강도/archive 공식 단일화 (finding #5, 데이터 의존)

데이터 레이어가 저장하는 컬럼(decay_S/importance/access_count/last_accessed/confidence)으로 두 공식이 **모순 없이** 파생되도록 고정:

- **표시 강도 S (글리프 밴드, SPEC §6/§8.5 canonical):**
  `retrievability = e^(−Δt_last_accessed / decay_S)`;
  `S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))`.
- **밴드 4단(ux §8.2 채택, SPEC §6를 4밴드로 갱신):**
  S≥0.66 🌳evergreen · 0.33≤S<0.66 🌿budding · 0.10≤S<0.33 🌱seedling(FADING) · S<0.10 ember(FADING, archive 임박).
- **archive 결정 = S < 0.10** (= ember 밴드 floor). 글리프와 archive가 **같은 S에서 파생**되므로 "🌳인데 archive 임박" 모순 불가(finding 해소). + capacity cap 초과 시 최저 S부터. pin이면 면제.
- at-risk(○) 강등: `confidence<0.4 OR Δt_last_accessed>60d` (밴드 무관 오버레이).
- consolidate decay-score(§6 step1, archive 후보 클러스터링용)는 내부 정렬 신호로만, **사용자 표시·archive 게이트는 위 S 단일 사용**. ux §4.3의 .42/.30/.18은 폐기(S로 통일).

---

### 9. Store 인터페이스 (최종, 시그니처)

```python
class Store(Protocol):
    def connect(self) -> None
    def migrate(self) -> None
    # write (모두 단일 tx + events co-write)
    def add_note(self, note: Note, emb: bytes | None) -> None
    def supersede(self, old_id: str, new: Note, emb) -> None
    def correct(self, id: str, new: Note, emb) -> None
    def update_note(self, id: str, **fields) -> None       # +note_history
    def archive(self, id: str) -> None
    def purge(self, id: str, cascade: bool) -> dict          # §7, returns proof
    def add_edge(self, e: Edge) -> None
    # read
    def knn(self, emb: bytes, k: int, scope, statuses=("active",)) -> list[tuple[str,float]]
    def bm25(self, query: str, k: int, scope, statuses=("active",)) -> list[tuple[str,float]]
    def get_notes(self, ids: list[str]) -> list[Note]
    def neighbors(self, id: str, hops=1) -> list[Edge]
    def get_held(self, scope) -> list[Note]
    def as_of(self, ids: list[str], at: datetime) -> list[Note]  # bi-temporal
    # side-effects
    def touch(self, ids: list[str], kind="search") -> None  # access_count++/last_accessed/decay_S++/access_log
    def set_held_for_human(self, id: str, reason: str | None) -> None
    # jobs
    def enqueue(self, kind: str, payload: dict, dedup_key: str | None, run_after=None) -> str
    def claim_job(self, worker: str) -> Job | None
    def finish_job(self, id: str, status: str, error: str | None) -> None
    # meta
    def get_meta(self, key: str) -> str | None
    def set_meta(self, key: str, value: str) -> None
```

`knn`은 numpy 기본: `embedder_id=meta.current`인 BLOB 전부 로드(scope/status 사전 SQL 필터) → float32 reshape → 코사인 → top-k. 개인 스케일(수천~수만)에서 brute-force 충분. `[vec]`면 가상테이블 MATCH로 위임.

---

### 10. Admission tie-break LLM strictly-local (finding #11)

SPEC §4에 **testable invariant** 추가: admission redaction의 모호-span tiebreak LLM은 `meta.embedder_id`/extraction provider와 **무관하게 항상 로컬 모델**(Ollama/llama.cpp). 코드 가드: `admission.tiebreak_llm`은 `LocalLLM` 타입만 허용, remote provider 주입 시 `assert isinstance(llm, LocalLLM)` fail. candidate-secret span은 non-local endpoint로 절대 전송 금지(eval: mock remote LLM이 호출되면 테스트 실패).
