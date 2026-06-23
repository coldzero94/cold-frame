# Coldframe (cold-frame) 결정 로그 (ADR)

> 프로젝트명 = **Coldframe** (`cold-frame`). 이전 코드네임 = cold-memo → 아래 초기 결정(D1~D18) 텍스트의 'cold-memo'는 **이름 변경 전 동일 프로젝트** 기록(의도적 보존).


> 각 결정의 날짜·상태·대안·근거를 추적. 번복 시 새 행 추가하지 말고 **상태만 갱신**(이력 보존).
> 상태: ✅DECIDED / 🔄IN-PROGRESS / ⏸️DEFERRED / ❌SUPERSEDED

| ID | 날짜 | 상태 | 결정 | 검토한 대안 | 근거(근거 문서) |
|---|---|---|---|---|---|
| **D1** | 06-21 | ✅ | **전략 = 하이브리드**: 검증된 엔진/패턴은 차용, 비어 있는 "메모리 두뇌"는 직접 구축 | 통째 USE / 통째 BUILD / fork | 코드 분석 결과 충돌해결·망각·dedup이 어디서도 제대로 구현 안 됨 → 직접 구축이 정당 ([source-analysis], [blueprint]) |
| **D2** | 06-21 | ✅ | build-vs-use는 **컴포넌트별** 결정(전역 이분법 아님) | 단일 전역 선택 | storage/reranker/BM25는 어차피 USE, "순수 BUILD" 없음 ([build-vs-use-decision] §1) |
| **D3** | 06-21 | ✅ | 저장 = **SQLite-first**, 출시 시 Postgres+pgvector 어댑터 추가. 스키마 portable | 처음부터 Postgres / 둘 다 동시 | 현 용도=로컬 개인. `store/` 추상화로 마이그레이션은 어댑터 교체 ([design] §1.1) |
| **D4** | 06-21 | ✅ | 패키징 = **코어/서버 하드 분리** + 오프라인 기본(`HashEmbedder`, 키 0) | 단일 패키지 | 로컬 설치 간단 + 무거운 서버 분리. 코어는 fastapi/psycopg import 금지 ([design] §1.2) |
| **D5** | 06-21 | ✅ | 배포 = **uv 주력**(로컬엔 Docker 안 씀). 컨테이너는 서버 레이어만(Podman/Docker/Nix) | Docker-everywhere / pipx | Docker는 서버 재현용이지 로컬 앱 설치용 아님. 단일바이너리(후)는 PyApp ([design] §1.3) |
| **D6** | 06-21 | ✅ | **완전 로컬·논스톱(DB 포함)** + **기록 뷰어**(CLI + 프로그램 화면) | 헤드리스만 | "로컬에서 자유롭게 + 기록이 어떻게 쌓였는지 봐야" ([design] §1.4) |
| **D7** | 06-21 | ✅ | 노트 1급 단위 = **atomic fact + 경량 SQL edge**. 그래프 DB는 멀티홉 필요 전까지 X | Zettelkasten note / graph triple | 개인 메모리에 균형적, Neo4j 운영비 회피 ([design] §2, [blueprint] §1.1) |
| **D8** | 06-21 | ✅ | write = **하이브리드**(파이프라인 추출 기본 + LLM self-edit 도구). 둘 다 공통 `WriteCore`로 수렴 | 파이프라인만 / self-edit만 | 결정성(파이프라인) + 유연성(self-edit). Letta는 두 경로 갈라져 문제 ([design] §3·§4) |
| **D9** | 06-21 | ✅ | **procedural memory v1 포함**(프롬프트 자가최적화) | 사실/시맨틱만 | LangMem의 검증된 차별점, 행동 메모리 ([design] §8, [blueprint] §1.12) |
| **D10** | 06-21 | ✅ | 메인 언어 = **Python + uv**. `Store.knn()/bm25()` 어댑터 시임 신성불가침, hot path는 eval이 병목 증명 후에만 PyO3/ANN 이식 | Rust / Go / TS / upfront hybrid | 워크로드 네트워크-바운드라 호스트 언어 속도 무의미; hot 루프는 이미 컴파일(numpy/FTS5); Python이 이기는 축=7종 이식+eval 반복속도. **대안 Go**(비개발자 단일바이너리 하드요구 시 1순위), **Rust 기각**(over-engineering) ([language-decision]) |
| **D11** | 06-21 | ✅ | Claude Code 연동 = **MCP 서버**. 로컬=**stdio(OAuth 불필요)**, 원격=HTTP(OAuth 2.0/2.1 지원). 도구 `search_memory`/`add_memory`/`summarize`; 세션시작 훅·리소스로 주입 | 커스텀 플러그인 / 직접 REST 호출 | MCP가 Claude Code 표준이고 로컬 stdio가 "전부 로컬·논스톱"과 정합(인증 0). 검증됨([design] §13). Python이 MCP SDK first-class → D10 보강 |
| **D12** | 06-21 | ✅ | UX 히어로 = **상태(망각/믿음 변화)**, NOT 토폴로지. **전역 그래프 거부**(hairball). 프라이머리=list/card "지금 아는 것" 인스펙터 + **as-of 타임-트래블(Belief-Fork)** + in-context recall-receipt. 그래프는 로컬 1–2 hop ego 렌즈만 | 전역 force-graph 센터피스 / 수작업 캔버스(Heptabase) | 리서치: graph-view는 200노드↑ hairball·무용(Tana는 graph 없이도 성공). cold-memo 고유 데이터(bi-temporal/decay/supersedes)만 가능한 "보이는 망각+되감는 믿음"이 진짜 혁신 ([ux-design]) |
| **D13** | 06-21 | ✅ | **원스톱 설치/셋업**: `uv tool install cold-memo` (1명령) → `cold-memo setup`(DB 자동init + Claude Code MCP 자동등록 + 상태). MCP 진입점=`cold-memo mcp` 콘솔스크립트. DB 첫실행 자동생성, 오프라인 기본, 네이티브 확장 불요 | 수동 다단계 / Docker | "프로그램처럼 원스톱" 요구. setup은 멱등, `claude` 없으면 `.mcp.json` fallback ([design] §1·§8) |
| **D14** | 06-21 | ✅ | 비주얼 미학 = **Dark Minimal + Life (Linear×Arc)**. 다크 우선, decay=빛/glow(흐려짐), 절제된 글리프(이모지 남발 X), 시그니처=as-of/belief-fork를 아름답게. 컨셉(살아있는 메모리)+실행(California-startup 미니멀 폴리시) | 라이트 에디토리얼(Stripe×Notion) / 플레이풀(Family×Raycast) | 사용자 선택. 다크 캔버스가 "glow/fade decay 시각화"에 최적이라 차별점이 가장 혁신적으로 보임. 토큰/상세 [ux-design] Part 3 |
| **D15** | 06-21 | ✅ | **write 전 admission 파이프라인** CLASSIFY→REDACT→CONFIDENCE-GATE→CONSENT. secret=gitleaks식 regex+엔트로피, PII=Presidio, LLM은 모호 span만·strictly local. secret=BLOCK(디스크 안 닿음)/PII=REDACT/그 외 ALLOW | post-hoc 스캔 / 사용자 규율 / LLM-only 탐지 | secret이 디스크에 닿기 전 차단(ChatGPT 실패모드 회피). [product-strategy] §1, SPEC §4 |
| **D16** | 06-21 | ✅ | provenance 강제 invariant + confidence 게이트 · forget+cascade(`derived_from`) · encryption=opt-in SQLCipher(키=OS 키체인, `.db` 옆 금지, 기본 평문) · **secret=hard-purge 예외**(archive-not-delete 깸) | 항상 평문 / 항상 암호화 | 소유·신뢰 서사 보존. [product-strategy] §1, SPEC §6·§9 |
| **D17** | 06-21 | ✅ | sync: **v1=pure-local + 결정적 export/import**. fact/edge를 append-only 이벤트 로그(materialized view)로 v1 선반영. v2=e2e log-shipping over BYO storage(머지=기존 bi-temporal). live `.db` sync 금지. cr-sqlite/Turso는 shortlist만 | whole-file sync / cr-sqlite 엔진 / hosted DB | bi-temporal이 cross-device 충돌을 hero(as-of)로 전환. [product-strategy] §2, SPEC §3 |
| **D18** | 06-21 | ✅ | 추출=단일콜(mem0 self-contained+Graphiti anti-generalization+MemOS type 융합) + **DURABILITY GATE**(durable만 영속, ephemeral drop, <0.4 hold). LLM 제안·코드 처분. confidence≠importance. merge=episodic→semantic만·비파괴 | LLM-only(langmem식) | corpus precision이 recall을 똑똑하게(랭킹 아님). [product-strategy] §3, SPEC §4·§7 |
| **D19** | 06-21 | ✅ | 포지셔닝='**anti-dossier**', pitch="Own the model of you — a local memory you can read, edit, and rewind, built into Claude Code." moat=corpus 전환비용+portable 포맷 spec+Claude-native+rewindable-belief UX. **이름 확정 = Coldframe(`cold-frame`)** — 콜드프레임=정원 틀(메모리 가꾸기 UX) × cold 정체성(coldzero) × cold-storage 의미. 폴더/패키지/CLI/db(`~/.cold-frame/`)/MCP 전부 `cold-frame`. (전략의 'avoid cold' 일반론은 창업자 브랜드가 override). 상표/.com/PyPI 클리어런스 권장(미확인) | mem0/ChatGPT-memory 류 포지션 | [product-strategy] §4 |
| **D20** | 06-21 | ✅ | 웹 UI 스택 = **Vite + Vue 3(TS) + UnoCSS** SPA (antfu/skills 활용). 코어(Python)는 read-mostly JSON API + 정적 SPA 서빙(`cold-frame ui`, `[ui]` extra). **빌드 번들 동봉 → 사용자 Node 불필요**(원스톱 유지). 코어/제품서버 분리 유지 | React+Next / 서버렌더 Python(HTMX) | 다크-미니멀+UnoCSS 궁합, antfu 스킬이 그대로 커버, 경량. Web Interface Guidelines(ux §8.9) 검증 통과. [design] §9 |
| **D21** | 06-21 | ✅ | **하드닝 blocking decisions 확정(B1~B7, wf_9cebead0)**: B1=이벤트로그 v1=**co-written append-only 감사로그**(notes=SoT, 같은 txn → §4 in-place 유지, export=로그덤프; D17 "materialized view" 문구 정정) · B2=secret purge=**crypto-shredding**(per-event 키 파기)+store 전수 열거+honest scope(live 파일만) · B3=token counter=**dep-free 보수 추정기 기본**(estimate≥actual)+eval은 tiktoken `[openai]` · B4=quarantine=**`pending` status 추가** · B5=이름=cold-frame(D19; 전략의 retire안 SUPERSEDED) · B6=**sync 코어 + 얇은 to_thread async facade**(MCP만 async seam) · B7=write-path=결정적 단계(추출/regex admission/dedup) inline·LLM 단계 jobs 큐 deferred+노트 provisional | 각 대안 risks.md B1~B7 | 적대적 감사 22리스크(C1~C4 포함) 해소 토대. 상세 [risks] + build/*.md |
| **D22** | 06-21 | ✅ | 코딩 셋업: **코드 스타일 = PEP8 + 전체 type hints**(ruff[ANN 포함]+ruff-format + mypy --strict + pre-commit, pyproject 설정) · **G2 quarantine = flag 컬럼**(Status 3-value 유지 + held_for_human/quarantined/triage_reason; D21-B4 'pending status' superseded) · **로컬 UI 포트 = 27182**(흔하지 않은 기본값 + 점유 시 자동 fallback + 127.0.0.1 + ui.port 파일 + doctor; DB는 SQLite라 포트 없음) | pending-status(G2 대안) / 7717 포트 | 타입안전·마찰0 로컬설치. CLAUDE.md §9, [design] §9 |
| **D23** | 06-22 | ✅ | P1 MCP 서버: `mcp` SDK + `anyio`를 **`[mcp]` extra**로 분리(코어 deps=pydantic+numpy 불변, I9). `cold_frame/mcp.py`만 `async def`(I4 단일 async seam); 도구 핸들러는 sync `Memory` 호출을 `anyio.to_thread.run_sync`로 래핑(로직은 sync `_*_impl`에 단일 구현). SDK/anyio는 **lazy import**(import-guarded) → 모듈 import가 무거운 deps를 코어로 안 끌어옴; 미설치 시 `main()`이 설치 힌트 출력 후 exit 2. 도구=`search_memory`/`add_memory`(P1), 결과에 `fact_deeplink` 포함 | mcp/anyio를 코어 dep로 / async 로직 복제 | D11(로컬 stdio MCP)·I4·I9 구현. extra이나 추적 위해 ADR화 |

| **D24** | 06-23 | ✅ | **`WriteCore.commit`은 fact 단위 원자성**(per-fact atomic)으로 확정. 추출 1메시지→N후보일 때 각 후보는 자신의 단일 txn(I3)으로 commit; 중간 후보 실패 시 앞선 유효 fact들은 롤백되지 않고 남음(best-effort-per-fact). 이유: `_classify`는 dedup/conflict **LLM 호출**을 포함하고, CLAUDE.md 규칙상 LLM I/O는 write txn **밖**에서 실행 → 전체 배치를 한 txn으로 묶으려면 모든 LLM 분류를 먼저 버퍼링하는 별도 리팩터가 필요하고, 각 fact가 독립적으로 유효하므로 부분 커밋이 데이터 오염이 아님. P6 리뷰의 "multi-candidate atomicity" deferred 항목을 **부채가 아닌 결정**으로 해소. 함께 정리한 deferred: jobs `finish/fail_job` locked_by fencing(D1), `update_note` 낙관적 버전락(D2), doctor FTS5 integrity-check + stale-vector 카운트(D3) | 전(全)배치 단일 txn(LLM 분류 선버퍼링) | 노트 단위는 이미 원자적(I3). 드문 다중-fact 부분실패는 의도된 시맨틱. [build/api-contract] §4 |

| **D25** | 06-23 | ✅ | **v1은 보안 ADMISSION(비밀 BLOCK + PII REDACT) + 비밀 hard-purge를 구현하지 않음 — descope.** 근거: cold-frame v1은 **로컬·단독·본인 소유 단일 파일**(`~/.cold-frame/memory.db`)이라 멀티테넌트 노출면이 없고, "비밀은 사용자가 직접 관리"(안 넣으면 그만 + 파일/노트 삭제로 제거)가 합리적. 따라서 **I6(admission before disk)/I7(admission LLM local-only)는 v1에서 미적용**, 상시 게이트(`test_purge_leaves_no_residue`/`test_no_secret_to_remote`/`test_admission_tiebreak_rejects_remote_llm`)도 v1 비적용 — D15/D16/security-spec §1의 BLOCK/REDACT/purge는 **v1.1 또는 hosted 멀티테넌트 [server] 레이어로 이연**(거기서 진짜 필요). **주의(정직성):** admission이 없으므로 (a) 사용자가 비밀을 `add`하면 평문 저장됨, (b) host-sampling(SamplingLLM) dedup/conflict 판정 시 노트 내용이 host 모델로 전송될 수 있음(단 host=대화를 이미 가진 같은 에이전트라 신규 노출 아님). README의 "secrets blocked" 문구는 거짓이라 정정함. **남은 출시 차단(보안 무관, 여전히 필요):** publish gate(이름/상표/PyPI, D19) + LICENSE 파일 + worker 상시 루프 + backup-before-migrate. P1–P6 빌드 표에 admission이 배정된 적 없었음(스케줄 안 된 갭이었고, 이제 명시적으로 이연 결정). | A안=출시 전 보안 moat 구현 / C안=인프라 먼저 | I6/I7 약화는 ADR 필수(CLAUDE.md §8). 감사 wf_6f18ae1c. [security-spec] §1, [SPEC] §4 |

## 결정 대기/후행
- ✅ **마이크로 결정 확정(SPEC)**: Triage 진입 기준(§6) · 표시 강도 S/글리프 밴드(§6) · MCP 도구 스키마(§8) · eval 구체 케이스(§10) · accent=iris `#7C5CFF`(잠정, 브랜드 단계 확정). 노트 입자(D7+§2)·프로그램 화면(D12)·언어(D10)도 결정 완료.
- 🔄 **리서치/디자인 중(다른 영역, wf_36992b95)**: 신뢰·프라이버시·보안(LLM-written 메모리) · 멀티디바이스/sync 입장 · 메모리 품질·프롬프트 · 포지셔닝/네이밍(코드네임 cold-memo → 실제 이름).
- ⏸️ **멀티테넌트 서버 언어**(후행·분리): 같은 `Store` 인터페이스 뒤 독립 결정(필요 시 Go/Rust).
- ⏸️ **임베딩 cross-tier 일관성**: HashEmbedder(기본) → `[openai]`/`[local-llm]` 업그레이드 시 재임베딩 마이그레이션.

[source-analysis]: analysis/source-analysis/RAW-source-analysis.md
[blueprint]: analysis/blueprint-combine-the-best.md
[build-vs-use-decision]: analysis/build-vs-use-decision.md
[design]: analysis/design.md
[language-decision]: analysis/language-decision.md
[ux-design]: ux-design.md
[product-strategy]: product-strategy.md
[risks]: risks.md
