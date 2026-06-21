# cold-frame 제품 전략 & 디자인 종합 문서

> 4개 탐색 영역(신뢰·프라이버시 / sync·포터빌리티 / 메모리 품질·프롬프트 / 포지셔닝·네이밍)을 하나로 종합.
> 기준선: `docs/SPEC.md`, `docs/decisions.md`(D1~D14), `docs/ux-design.md`. 이 문서는 wf_36992b95(decisions.md L25 IN-PROGRESS 4영역)의 산출물이며, 확정 시 D15~D18로 승격 예정.

## 0. 한 줄 종합

cold-frame의 진짜 해자는 단일 기능(망각·bi-temporal·로컬-퍼스트 — 각각 이미 경쟁자에게 존재)이 아니라 **"읽고·고치고·되감고·소유하는 메모리(the memory you can read, edit, rewind, and own)"라는 통제·소유 스탠스의 조합**이다. 4개 영역은 모두 이 한 문장을 지지하도록 정렬된다: 프라이버시(소유의 신뢰 근거) · sync(소유를 깨지 않는 이동성) · 메모리 품질(소유할 가치가 있는 corpus) · 포지셔닝(소유라는 구매 이유).

---

## 1. 신뢰·안전·프라이버시 (→ SPEC §4·§6·§9에 추가)

### 권고 (recommendation)
LLM이 쓰는 로컬 개인 메모리의 최대 리스크는 둘이다: (1) **절대 남으면 안 되는 것**(secret/credential/PII)을 캡처, (2) **틀린/환각 주장**을 사실로 영속화. 해답은 **모든 write 이전에 도는 4단계 admission 파이프라인**: `CLASSIFY → REDACT → CONFIDENCE-GATE → CONSENT`. 이는 SPEC §4 WriteCore의 `[DEDUP]·[CONFLICT]·[PERSIST]` **앞단에 삽입**되는 게이트다. 캡처 시점 redaction은 ChatGPT(서버 보관·redaction 0)·Limitless(클라우드 크리프) 대비 **구조적 차별점**이며, "write 전에" 도므로 secret이 디스크에 닿지 않는다.

탐지는 검증된 오프라인 Python 도구를 모방: secret = gitleaks 스타일(regex 룰 + Shannon 엔트로피 ~4.0 bits/char, 20자↑ 토큰, keyword-gated로 오탐 억제), PII = Microsoft Presidio(NER + regex + context). **LLM 게이트는 모호 span에만 도는 느린-경로 tiebreaker일 뿐, 단독 방어선이 절대 아님**(결정성·오프라인 기본 보존 = D4·§4 철칙과 정합).

### 제안 결정 (crisp)
- **D-T1**: 모든 write 전에 의무적 admission 파이프라인(CLASSIFY→REDACT→CONFIDENCE-GATE→CONSENT). 통과 못 한 사실은 영속화 안 됨.
- **D-T2**: secret 탐지 = gitleaks식 regex+엔트로피, PII 탐지 = Presidio. 룰은 user-editable TOML(allow/deny). LLM은 모호 span 한정 보조.
- **D-T3**: 3-결과 정책 — **BLOCK**(secret/credential/key: 아카이브조차 안 함, "credential redacted at <time>" 무가치 tombstone만), **REDACT**(PII → `<PERSON_1>` 타입 placeholder, 원본 비영속), **ALLOW**. 고신뢰 secret 매치는 default-deny.
- **D-T4**: provenance를 강제 invariant로(모든 fact row에 `source_ref + extractor + extracted_at`). provenance 없는 LLM-written 사실은 high-confidence 불가 → 가시적 quarantine(pending)으로, 기본 검색 제외. (SPEC §6 Triage 진입 (c) confidence<0.4와 직결)
- **D-T5**: "이것과 거기서 파생된 모든 것 잊기" = `derived_from` 엣지 cascade(bi-temporal 트랜잭션). 일반 사실은 archive/tombstone, **SECRET/PII 플래그 사실은 hard-purge 모드**(VACUUM + overwrite + post-purge grep 검증, secure_delete pragma, WAL checkpoint) + 증명 반환.
- **D-T6**: encryption-at-rest = **opt-in SQLCipher**(AES-256 full-DB), 키는 OS 키체인(macOS Keychain/Secure Enclave 우선; libsecret/DPAPI 후행)에 봉인, **`.db` 옆에 절대 저장 안 함**. **기본은 비암호화 유지**(grep 가능·"당신이 소유하는 한 파일" 서사 보존, 암호화는 `cold-frame init --encrypt` 1-플래그 업그레이드).
- **D-T7**: 로컬 웹 뷰어가 Redaction Log + per-fact 트러스트 패널(provenance·confidence·캡처시각·redaction 배지) + tombstone 미리보기 있는 "forget+cascade" 노출. **VISIBLE FORGETTING은 "기억하기를 거부하는 것도 보이게" 포함.**

### 보류 (defer)
- secret 탐지 strictness 기본값(aggressive vs balanced) — aggressive-by-default + 쉬운 allowlist로 기울되 베타 데이터로 캘리브레이션.
- REDACTED PII의 reversible 매핑(본인 un-redact 가능) vs one-way — 공격 표면 vs 효용 트레이드오프, v1은 one-way 권장.
- SQLCipher 키 회전/복구 UX(키체인 분실 시 passphrase fallback vs 데이터 손실).

### 충돌 플래그
- **D4(오프라인 기본·키 0)와 정합**: LLM 게이트를 단독 방어선으로 쓰지 않으므로 keyless add(R11) 깨지지 않음. 단, **LLM 게이트는 strictly local(Ollama/llama.cpp)로 한정**해야 D4 위반 안 함 — 명시 권고.
- **archive-not-delete(§9 불변 규칙)와 부분 충돌**: secret만은 archive-not-delete의 예외(hard-purge). 일반 사실은 규칙 유지. → 불변 규칙에 "secret은 hard-purge 예외" 주석 추가 필요.

---

## 2. 멀티디바이스 sync & 포터빌리티 (→ §3·§4 일부 + 대부분 별도 전략 ADR)

### 권고 (recommendation)
cold-frame의 bi-temporal event-sourced 모델은 sync의 지배적 사실이다. 이 모델은 **whole-file sync에 적대적**(Dropbox/iCloud/Syncthing는 열린 SQLite+WAL을 손상시키고 진짜 머지 없음 = silent loss)이지만 **log/event-shipping sync에 유난히 우호적**: append-mostly bi-temporal 스토어는 이미 CRDT 형태이고, 충돌은 transaction-time 우선순위로(파괴적 overwrite 없이) 해소되며, "as-of" 타임트래블이 cross-device 머지를 감사 가능·되감기 가능하게 만든다.

**v1 스탠스 = PURE-LOCAL**. 유일한 포터빌리티 = content-addressed export/import 번들(manifest + events.ndjson/sqlite + sha256). live sync 없음. **v2 = opt-in BYO-storage e2e-암호화 sync, 우리 자체 append-only fact/edge 이벤트 로그를 dumb blob store 위로 shipping**(파일 복사 X, hosted multi-writer DB X). 머지 함수 = 우리의 **기존 bi-temporal resolver**(transaction-time precedence). 이건 Obsidian/Logseq/Anytype의 검증된 "변경을 sign+encrypt → 불투명 blob을 BYO 스토리지로 → 로컬 머지" 패턴과 매핑된다.

**cr-sqlite를 1차 메커니즘으로 채택하지 않음**: FK 강제 불가(우리 typed edge = FK 그래프인데 치명적), syntactic per-column LWW가 우리의 semantic belief 머지를 모르고 provenance를 버림. cr-sqlite/libSQL/Turso는 가속기 shortlist로만 보관(트리거 조건 명시한 ADR로 기각 사유 박제).

### bi-temporal이 머지에 주는 이점
cross-device 불일치가 "또 하나의 belief(transaction-time 부여)"가 되어, **히어로 기능(rewindable belief / as-of)이 "머지 전 device A가 믿은 것 vs B"를 그대로 시각화**한다. sync의 가장 무서운 속성이 제품의 hero 데모로 전환된다. decay/consolidation/embedding 등 파생 상태는 **sync 안 함**(로컬 재계산, HashEmbedder는 결정적이라 재현 가능).

### 제안 결정 (crisp)
- **D-S1**: v1 = pure-local. 유일한 멀티디바이스 스토리 = 결정적 export/import content-addressed 번들.
- **D-S2**: live `.db` 파일을 절대 sync 안 함. export/backup은 닫힌/WAL-checkpointed read-only 스냅샷(또는 logical dump)에만. "cold-frame.db를 Dropbox/iCloud에 두는 것은 미지원"을 크게 문서화.
- **D-S3**: **fact/edge 스토어를 명시적 append-only 이벤트 로그로**(per-event `device_id` + HLC/Lamport + content hash), 현재 row는 derived materialized view. **sync 존재 전 v1에서 미리 시행** — export/backup/future-sync를 같은 primitive로 만드는 최소 보험.
- **D-S4**: v2 sync = 자체 append-only LOG-SHIPPING 프로토콜 over BYO dumb storage(S3 호환 / iCloud·Dropbox 폴더 = blob만 / synced folder), client-side e2e(device Ed25519 서명 + account symmetric 키 scrypt/argon2). hosted 서버 불요.
- **D-S5**: cross-device 충돌 = bi-temporal resolution 재사용 + UI 노출. 동시 assertion에 머지 시 HLC 부여, valid-time 겹침은 transaction-time precedence, 둘 다 as-of로 조회 가능. 동률 tie-break = (hlc, device_id, content_hash).
- **D-S6**: 파생/임시 상태(decay·consolidation·embedding·FTS)는 sync 안 함. canonical 이벤트 로그만 sync, 각 디바이스가 로컬 재계산.
- **D-S7**: cr-sqlite·libSQL/Turso는 문서화된 research shortlist로만(자체 log-shipping이 스케일에서 불충분할 때만 재평가; 트리거 = 실시간 협업, >~5 디바이스, sub-second 수렴).

### 보류 (defer)
- identity/key 모델(account-spanning vs QR/passphrase pairing) — e2e 키 교환 설계 좌우.
- 로그 compaction/GC — append-only는 무한 성장. snapshot+truncate 시점과 "visible forgetting"이 synced 로그를 prune하는지 vs materialized view만인지.
- v2 스토리지 어댑터 우선순위(S3 vs 폴더-of-blobs) — 폴더-of-blobs가 가장 단순·불변 blob이라 손상 회피.

### 충돌 플래그
- **D3(SQLite-first, 출시 시 Postgres 어댑터)와 정합**: log-shipping은 Store 어댑터 위에 얹히므로 충돌 없음. Turso/libSQL을 엔진으로 채택하면 D3 위반 → shortlist 보류로 회피.
- **D-S3(append-only 이벤트 로그로 리팩터)는 §2 데이터 모델·§3 저장 레이어에 실제 변경**을 요구 — v1 스코프 영향. 단 SPEC §2가 이미 `history`/bi-temporal/`supersedes`를 가지므로 이벤트-로그화는 큰 도약이 아님. **SPEC §3에 "current rows = materialized view of append-only event log" 명문화 권고.**

---

## 3. 메모리 품질 & 프롬프트 (→ SPEC §4·§6·§7·§10 정밀화)

### 권고 (recommendation)
메모리 품질은 **write 측이 게이트**다(read 측 §5는 이미 구축됨). 참조 엔진은 교훈적으로 실패한다: mem0는 self-contained 추출 프롬프트만 있고 dedup/conflict 코드 강제 **제로**; Graphiti만 진짜 bi-temporal dual-candidate conflict 프롬프트 + **결정적** freshness 보유; MemOS는 fact-unit MERGE 프롬프트가 있으나 **dead code**; LangMem은 모든 추론을 LLM 한 콜에 위임(결정성 0).

설계 = **mem0 shape + Graphiti anti-generalization + MemOS type-unit 분류를 하나의 추출 프롬프트로 융합**, cold-frame의 정확한 Note 필드를 emit. LLM은 후보-제안 + 모순-판정 + episodic→semantic 요약에만; **모든 freshness/merge 결정은 결정적 코드로**(SPEC §4 철칙). 최고 레버리지 단일 레버 = **추출의 DURABILITY GATE**: durable한 identity/preference/decided 사실은 영속화, ephemeral chatter는 drop. **랭킹이 아니라 corpus precision이 recall을 똑똑하게 느끼게 한다.**

### 추출 정책 (무엇을 기억 / 무시)
- **EXTRACT(durable)**: identity("Anthropic에서 일함"), 안정적 선호("다크로스트 선호"), 결정/약속, 관계, 반복 절차, 지속 관련성 있는 episodic 사건.
- **IGNORE/저신뢰(ephemeral)**: 일시 상태("오늘 피곤"), in-task chatter, 질문, 툴-출력 요약, 가정, 예의치레.
- **confidence 매핑**: durable identity/decision ≥0.8 · 명확한 선호 0.6~0.8 · 추론/hedged 0.4~0.6 · speculative <0.4. **코드 post-filter: confidence<0.4 → hold_for_human**(§6 Triage (c)), silent persist 금지.

### precision / recall
recall precision은 대부분 **추출 품질 문제**(read 측 구축됨). 레버 순서: (a) durability gate가 corpus를 유용한 사실로 dense하게 → distractor 적중 감소; (b) self-contained 리라이팅 → 대화 없이 독립 임베딩/검색 가능; (c) 같은 콜에서 keyword 추출 → FTS5(§2 note_fts) 직접 공급; (d) anti-generalization이 discriminative 토큰(브랜드/숫자) 보존 → BM25 정밀; (e) confidence+decay가 저가치 노트 archive → active set 고정밀. **랭킹 튜닝 전에 추출 precision/recall eval(§10) 먼저 실행.**

### 프롬프트 스펙 (3종)
- **EXTRACTION**(단일 LLM 콜, 결정적 JSON): INPUT = system role + OBSERVED_DATE(모든 상대시간 grounding, Current Date 아님) + SCOPE + LAST_K 원시 턴 + RECENTLY_EXTRACTED + EXISTING_MEMORIES(INT-ID 리스트, uuid→int remap으로 id 환각 차단, stable id는 모델에 절대 노출 X). OUTPUT/사실 = `{content(자기완결 단일절 15~80w, 대명사·상대시간 해소, 고유명사/숫자/브랜드 보존), memory_type∈{semantic,episodic,procedural}, keywords[], confidence, valid_at, importance, durable, attributed_to}`. HARD: standalone-verifiable만, 날조/echo/meta("user asked X") 금지, 복합문 atomic 분리, 특정 일반화 금지, 없으면 `[]`.
- **DUAL-CANDIDATE/CONFLICT**(Graphiti 패턴, 결정적 dedup tier 후 + 엔진 auto-resolve 실패분만): INPUT = NEW_FACT + DUPLICATE_CANDIDATES(같은 subject/predicate) + INVALIDATION_CANDIDATES. OUTPUT = `{duplicate_of, contradicts[], relation:'same'|'update'|'unrelated'}`. few-shot이 미묘 케이스 교육("Vessl에서 일함" vs "Anthropic에서 일함" = UPDATE/모순, NOT duplicate; "pizza 좋아함" vs "사랑함" = duplicate). **모델은 sameness/모순만 결정, 승자 절대 안 정함 — 코드가 valid_at 비교**(§4 LLM-forbidden freshness).
- **MERGE/CONSOLIDATION**(MemOS fact-unit, 백그라운드 §6): INPUT = 같은 토픽 episodic 클러스터. OUTPUT = `{summary, merged_from[], keep_separate[]}`. HARD: fact-unit decompose 먼저, **일시/one-off를 durable preference로 절대 머지 안 함**(MemOS 룰), 모순 시 최신값 보존, summary는 derived_from 엣지 가진 새 semantic 노트, 원본은 cold-archive(삭제 X). **episodic→semantic만, procedural 절대 collapse 안 함.**

### 결정적 티어링 (§4)
LLM이 충돌을 보기 전에: (1) uuid5(normalized) exact → drop; (2) 엔트로피 게이트 + MinHash/Jaccard 0.9 → drop; (3) 의미 코사인 ≥0.93 auto-merge, <0.82 distinct, **0.82~0.93만 dual-candidate LLM**(§6 Triage 임계와 정확히 일치). 같은-subject-다른-value 쌍만 추가로 LLM. → 비용 bound, LLM-mock 테스트 가능(§10).

### 제안 결정 (crisp)
- **D-Q1**: 단일-콜 추출 프롬프트가 cold-frame 정확한 Note 필드 emit(mem0 shape + Graphiti anti-generalization + MemOS 분류 융합).
- **D-Q2**: **DURABILITY GATE가 핵심 추출 정책**. durable 영속, ephemeral drop, confidence<0.4 → hold_for_human(silent persist 아님).
- **D-Q3**: "LLM이 제안, 코드가 처분" — LLM은 sameness/모순 판정 + summary만, freshness·archive·merge-commit은 결정적 코드(§4 철칙·§10 mock 결정성 보존).
- **D-Q4**: conflict/merge LLM 콜은 0.82~0.93 코사인 모호 밴드 + 같은-subject-다른-value만 발화, 그 외 결정적 auto-resolve.
- **D-Q5**: confidence(추출 확신)와 importance(장기 가치)를 **별도 필드 유지**. importance는 type+durability로 seed, feedback EMA(α=0.1)로 보정.
- **D-Q6**: merge/consolidation은 episodic→semantic만·fact-unit decompose·procedural collapse 금지·one-off를 durable preference로 머지 금지·원본은 derived_from + cold-archive(삭제 X).

### 보류 (defer)
- confidence rubric 캘리브레이션(durable-identity 기본 0.8 vs 그 이상; held-out 캘리브레이션 셋 필요성).
- episodic-with-lasting-relevance vs ephemeral 경계("오늘 피곤" vs "3주째 피곤" = durable 건강 신호) — golden example로 핀.
- keyword emission = 모델 생성(풍부·drift) vs 코드 파생(결정적·약함) vs 둘 다(코드를 floor).
- per-source 추출 정책 노출(CLI `--raw`는 durability gate 우회 vs chat은 완전 적용).

### 충돌 플래그
- **D8(write 하이브리드: 파이프라인 + self-edit, 공통 WriteCore) 완벽 정합** — 추출 프롬프트는 파이프라인 경로, durability gate는 WriteCore에 들어감.
- **§6 Triage 임계(0.82/0.93)·§7 procedural gradient·warrants_adjustment=False와 1:1 일치** — 충돌 없음, 정밀화일 뿐. chat 추출은 procedural 노트 auto-mint 금지(`optimize_prompt`만), D9 정합.

---

## 4. 포지셔닝·네이밍·moat (→ 전부 별도 브랜드 전략, SPEC 비포함)

### 권고 (recommendation)
방어 가능한 wedge = 단일 기능 신규성이 **아니라** **통제/소유 스탠스로 프레임한 조합**: "the memory you can read, edit, rewind, and own — built into Claude Code." 카테고리가 빠르게 붐빈다: 거의 정확한 기능적 쌍둥이(GitHub "memoirs": 로컬 SQLite + FTS5/sqlite-vec + bi-temporal as-of + Ebbinghaus decay + timeline/graph/conflict UI + Claude Code/Cursor용 MCP)가 이미 존재. 자명한 이름(mnemo, mnemon, engram×5, recall/recallify)은 다 선점됨.

⇒ (1) **기능 신규성이 아니라 PRODUCT POLISH + 날카로운 이념적 입장(anti-"dossier")으로 승부**; (2) 이름은 포화된 memory/neuro 어휘를 의도적으로 회피. 가장 강한 프레임 = Simon Willison의 널리 인용된 ChatGPT "memory dossier" 비판(불투명·통제 상실)의 **문자 그대로의 답**: provenance·visible forgetting·rewind 슬라이더로 "당신 자신의 모델을 당신에게 건네는 메모리".

### one-line pitch
> **"Own the model of you — a local memory you can read, edit, and rewind, built into Claude Code."**
> (당신 자신의 모델을 소유하라 — 읽고·고치고·되감을 수 있는, Claude Code에 내장된 로컬 메모리.)

### 경쟁 대비 wedge
ChatGPT(서버 보관·불투명 dossier)·Limitless(클라우드 크리프, Rewind는 2025-12 셧다운 = 클라우드 크리프가 신뢰를 침식한다는 교훈)·memoirs(엔진 클론)와 대비. forgetting은 top-line이 아니라 aha/데모(다운사이드처럼 들릴 위험), bi-temporal은 첫인상에 너무 추상적. **구매 이유는 "소유/통제", aha는 "되감기/망각".**

### moat (해자)
개별 엔진 기능이 **아니라**: (a) 사용자의 누적·버전·provenance-rich 개인 corpus = 전환비용 자산(data gravity); (b) **공개·발행·portable 파일 포맷 spec** = "당신이 소유한다"를 문자 그대로 검증 가능하게 하는 트러스트 아티팩트; (c) Claude-Code-native first-mover 폴리시; (d) rewindable-belief UX 장인정신. memoirs/Zep/Graphiti가 이미 엔진 기능(decay·as-of·vec+FTS·MCP)을 클론하므로 기능 신규성은 방어 불가.

### 이름 후보 (셋)
memory/neuro 어휘 이탈, "시간을 거슬러 읽어낼 수 있는 층층이 쌓인 기록(palimpsest)" 컨셉, 사전 단어보다 **coined ownable 마크** 선호:
1. **GLASSBOX** — anti-dossier(불투명 블랙박스의 반대 = 투명 유리상자) 직결, 강한 의미.
2. **PALIMPSEST → "Pal"** — 층층이 덮어쓰되 옛 층을 읽어낼 수 있는 = bi-temporal/rewind의 문학적 은유.
3. **CAIRN** — 길에 쌓아 표식이 되는 돌무더기 = 누적·소유·navigation; 짧고 ownable.
(코드네임 "cold-frame"는 cold-storage/차가움 연상으로 "살아있는 메모리" 브랜드를 저평가 → 실명에서 탈피 권고.)

### aha 모먼트 (온보딩 triad)
타깃 = 'context-controller' 파워유저, v1 beachhead = Claude Code 유저. aha = **5분 내 triad**: ① belief가 supersede됨 → ② as-of 슬라이더로 rewind → ③ recall-receipt 클릭으로 "왜 Claude가 그렇게 말했는지" 확인. provenance + rewind + visible forgetting을 한 번에 증명하는, 가장 공유 가능·차별적 데모.

### 제안 결정 (crisp)
- **D-P1**: 'anti-dossier' 포지셔닝 채택. primary pitch = "Own the model of you — ...".
- **D-P2**: memory/neuro 어휘에서 rename. mnemo/engram/recall/memoir-인접 이름 금지. palimpsest 컨셉의 coined ownable 마크 선호. 코드네임 cold-frame 폐기.
- **D-P3**: moat = (a) 사용자 corpus = 전환비용 + (b) 공개 portable 포맷 spec + (c) Claude-Code-native 폴리시 + (d) rewindable-belief UX — 개별 엔진 기능 **아님**.
- **D-P4**: 타깃 = context-controller 파워유저, v1 = Claude Code beachhead. 온보딩 aha = supersede→rewind→recall-receipt triad.

### 보류 (defer)
- 상표/.com/npm/PyPI 클리어런스(USPTO + 도메인 + PyPI — install이 `uv tool install <name>`이라 PyPI-free 필수). 이름 확정 전 정식 검색.
- 'dossier' 네임-앤-셰임 강도(경쟁사 약점에 앵커링 = 반응적으로 보일 위험; 런치 카피엔 OK, 지속 태그라인은 독립적으로).
- 공개 포맷을 day-1 정식 발행 spec vs "문서화된 단일 SQLite 파일"(v1은 후자, 제품화 시 전자) 권장.

### 충돌 플래그
- **D12·D14(UX 히어로 = belief-fork/as-of, Dark Minimal + Life)와 완벽 정합** — aha triad가 D12 히어로를 그대로 구현, anti-dossier가 D14 "살아있는 메모리" 미학을 강화.
- **이름 변경은 decisions.md L25·L27(코드네임 cold-frame → 실제 이름) 미해결 항목을 해소** — 충돌이 아니라 닫음.

---

## 5. 결정 요약 & 반영 위치

| ID | 결정 (crisp) | 반영 위치 | SPEC 섹션 / 전략 |
|---|---|---|---|
| D-T1 | write 전 의무 admission 파이프라인 (CLASSIFY→REDACT→GATE→CONSENT) | **SPEC** | §4 WriteCore 앞단 (신규 [ADMISSION] 단계) |
| D-T2 | secret=gitleaks식·PII=Presidio, LLM은 보조, 룰 TOML | **SPEC** | §4, §12 디렉터리(`cold_frame/admission/`) |
| D-T3 | BLOCK(secret tombstone)/REDACT(PII placeholder)/ALLOW | **SPEC** | §4, §2 데이터 모델(tombstone row) |
| D-T4 | provenance 강제 invariant + confidence 게이트 + quarantine | **SPEC** | §2, §6 Triage (c) |
| D-T5 | derived_from cascade forget + secret hard-purge(VACUUM+검증) | **SPEC** | §6, §9.1 `forget` 명령 |
| D-T6 | opt-in SQLCipher, 키 키체인 봉인, 기본 비암호화 | **SPEC** | §1 패키징, §3 저장 레이어, §9.1 `init/setup --encrypt` |
| D-T7 | 뷰어 Redaction Log + trust 패널 + forget 미리보기 | **SPEC** | §9 UX |
| D-S1 | v1 pure-local, export/import 번들만 | **SPEC** | §9.1 `export/import`(번들 포맷 명세) |
| D-S2 | live .db sync 금지, checkpointed 스냅샷만 | **SPEC** | §3, 문서 경고 |
| D-S3 | append-only 이벤트 로그 + current = materialized view (v1 시행) | **SPEC** | §2·§3 (실 스키마 영향) |
| D-S4 | v2 BYO e2e log-shipping sync | **별도 전략** | sync ADR (v2) |
| D-S5 | cross-device 충돌 = bi-temporal resolution + UI 노출 | **별도 전략** | sync ADR + §9 hero |
| D-S6 | 파생 상태(decay/embed/FTS) sync 안 함 | **별도 전략** | sync ADR |
| D-S7 | cr-sqlite/Turso는 shortlist만(트리거 명시 ADR) | **별도 전략** | 기각 ADR |
| D-Q1 | 단일-콜 추출 프롬프트 = 정확한 Note 필드 | **SPEC** | §4 [EXTRACT], `cold_frame/prompts/` |
| D-Q2 | DURABILITY GATE = 핵심 추출 정책 | **SPEC** | §4, §6 Triage (c) |
| D-Q3 | LLM 제안·코드 처분(freshness 코드) | **SPEC** | §4 철칙(기존 강화) |
| D-Q4 | conflict LLM은 0.82~0.93 밴드만 | **SPEC** | §4·§6(기존 임계와 일치) |
| D-Q5 | confidence ≠ importance 별도 필드 | **SPEC** | §2 데이터 모델, §6 decay |
| D-Q6 | merge = episodic→semantic·procedural 보호·비파괴 | **SPEC** | §6 consolidation, §7 |
| D-P1 | anti-dossier 포지셔닝 + pitch | **별도 전략** | 브랜드/포지셔닝 |
| D-P2 | rename(memory/neuro 어휘 이탈), cold-frame 폐기 | **별도 전략** | 네이밍(클리어런스 후 확정) |
| D-P3 | moat = corpus+포맷spec+CC-native+UX | **별도 전략** | 브랜드/moat |
| D-P4 | 타깃 = context-controller, aha = triad | **별도 전략** | GTM/온보딩(단 triad는 §9 UX와 연결) |

### 우선순위: SPEC에 접을 것 (v1 스코프 직접 영향, 순서대로)
1. **D-S3(append-only 이벤트 로그화)** — 가장 기초적인 스키마 결정, sync·export·backup·forget이 모두 이 primitive 재사용. v1에 미리 박아야 cr-sqlite 경로를 피함. (§2·§3)
2. **D-Q2(durability gate) + D-Q1(추출 프롬프트)** — 메모리 품질의 최고 레버, write 측 즉시 영향, read 측은 이미 구축. (§4)
3. **D-T1~T5(admission 파이프라인 + provenance + forget cascade)** — 프라이버시는 구조적 차별점이자 신뢰의 근거, write 전 게이트라 §4에 박혀야 함. (§4·§6·§9)
4. **D-Q3~Q6, D-T6~T7** — 기존 §4·§6·§9 정밀화(대부분 임계/필드 명세, 신규 구조 적음).
5. **D-S1·S2(export/import 번들 + live-db sync 금지 문서화)** — v1 포터빌리티, §9.1 명세.

### 별도 전략 관심사 (SPEC 비포함, 독립 트랙)
- **sync v2(D-S4~S7)** — 별도 ADR. v1엔 D-S3 primitive만 두고 sync 자체는 v2로 보류.
- **포지셔닝·네이밍·moat(D-P1~P4 전부)** — 브랜드/GTM 트랙. 단 D-P4 aha triad와 D-P1 anti-dossier는 §9 UX(D12 belief-fork)·D14 미학과 강하게 연결되므로 디자인-브랜드 동기화 필요.
- **상표 클리어런스** — 네이밍 확정의 차단 의존성, 엔지니어링과 병렬.

### 종합 충돌 점검 (기존 D1~D14 대비)
- 전반적으로 **충돌 없음 / 강한 정합**. 4영역은 D7(atomic fact + edge), D8(하이브리드 write), D9(procedural), D12(belief-fork hero), D14(미학)를 정밀화·강화한다.
- 주의 2건: (i) **archive-not-delete 불변(§9)의 secret 예외**(D-T5 hard-purge) — 불변 규칙에 명시적 예외 주석 필요; (ii) **LLM 게이트/추출은 strictly local**이어야 D4(오프라인 기본·키 0) 위반 안 함 — admission·extraction 양쪽에 명문화.
- **이름 변경(D-P2)은 decisions.md의 미해결 코드네임 항목을 해소**하므로 충돌이 아니라 종결.
