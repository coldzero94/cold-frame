# cold-frame 요구 추적 매트릭스 (Requirements Traceability)

> ⚠️ HISTORICAL — P1–P6 shipped in v0.1.0; the 🔲/🔄 status column is a pre-build snapshot, not current. See CLAUDE.md Status.

> ⭐ **"다른 제품 비교 → 우리가 필요한 부분 도출 → 그것을 만든다"**의 단일 출처.
> 각 요구 = *어느 제품의 강점/공백에서 도출*(근거) + *어디서 구현*(SPEC 위치). 우선순위 P0/P1/P2. 상태 🔲계획 / 🔄진행 / ✅결정.
> 근거 상세는 `analysis/`(소스해부·blueprint), 구현은 [`SPEC.md`](SPEC.md).

## A. 메모리 두뇌 (직접 구축 / 차용)

| ID | 요구 | 도출 근거 (비교) | 접근 | SPEC | 우선/상태 |
|---|---|---|---|---|---|
| **R1** | 결정적 충돌해결 + bi-temporal freshness | Graphiti만 실제 구현 / mem0 2-phase는 죽은코드(ADD-only) / 나머지 부재 | Graphiti bi-temporal 차용 + freshness 결정적(LLM 금지) | §4 | P0 🔲 |
| **R2** | 의미 dedup (string match 넘어) | mem0=md5만 / cognee=exact-name만 → 의미 중복 통과 | Graphiti 티어드 + 의미 near-dup 직접 | §4 | P0 🔲 |
| **R3** | 하이브리드 검색 + RRF | mem0(전역-divisor footgun) / Graphiti RRF / MemOS fan-out | 차용·조합 (vec+FTS5+edge→RRF) | §5 | P0 🔲 |
| **R4** | **token-budget 패커** | **7종 전부 미구현** | **직접 구축** | §5 | P0 🔲 ★ |
| **R5** | **효율적 저장 = 망각/consolidation** (무한 누적 금지) | 전부 부재/stub / A-MEM·mem0 무한 성장 | **직접 구축**(decay+요약+archive+cap) | §6 | P0 🔲 ★ 사용자강조 |
| **R6** | provenance/감사/버전 | cognee 재귀 stamping / MemOS version / Letta block history | 차용 | §2·§4 | P1 🔲 |
| **R7** | procedural memory | LangMem 차별점 | 차용(gradient+var-healer) | §7 | P1 🔲 |
| **R8** | 경량 그래프(edge) | A-MEM 개념(미materialize) / Graphiti | SQL edges 직접(그래프 DB X) | §2 | P1 🔲 |

## B. 통합

| ID | 요구 | 도출 근거 | 접근 | SPEC | 우선/상태 |
|---|---|---|---|---|---|
| **R9** | Claude Code 세션에서 검색/요약 | 사용자 요구 | MCP 서버. 로컬=stdio **OAuth 불필요**(검증), 원격=OAuth2.0 | §8 | P1 🔲 |

## C. 비기능 / 제품

| ID | 요구 | 도출 근거 | 접근 | SPEC | 우선/상태 |
|---|---|---|---|---|---|
| **R10** | 완전 로컬·논스톱(DB 포함) | 소유 / 시장 SaaS 종속 | SQLite 단일 파일 | §1·§2 | P0 🔲 |
| **R11** | 오프라인(키 0) | 주권/로컬 자유 | HashEmbedder 기본 | §1 | P0 🔲 |
| **R12** | 간단 로컬 설치(Docker X) — **원스톱** | 운영 부담 회피 / 사용자 요구("명령어 원스톱") | `uv tool install` 1명령 + `cold-frame setup`(DB 자동init + MCP 자동등록). 오프라인 즉시 동작, 네이티브 확장 불요 | §1 | P0 🔲 |
| **R13** | 코어/서버 하드 분리 | Letta=무거운 런타임 반면교사 | 패키지 경계 | §1 | P0 🔲 |
| **R14** | 기록 뷰어(CLI+프로그램 화면) | 사용자 요구 / 시장 도구 불투명 | CLI + 로컬 웹 UI | §9 | P1 🔲 |
| **R18** | 혁신적 메모리 시각화 (보이는 망각 + 되감는 믿음) | 사용자 요구 / 리서치: 경쟁 도구는 토폴로지만, 상태(decay·bi-temporal·supersedes) 시각화 부재 | 직접: list/card 인스펙터 + as-of 타임-트래블 + ego-graph + recall-receipt. 엔진 보강: `access_log`, `held_for_human` | §9 | P1 🔲 ★ |
| **R19** | 비주얼 미학: 예쁘고 심플 + California-startup 혁신 | 사용자 요구 | 미니멀·정제된 디자인 시스템(타이포/spacing/color/motion). living-memory를 Linear/Vercel급 폴리시로(이모지 남발 X). 히어로 = as-of/belief-fork를 아름답게 | §9, ux-design | P1 🔲 ★ |
| **R15** | 메인 언어: 속도·안정성 | 제품화 대비(사용자 제기) | **Python+uv**(Store 시임, hot path 후이식) | §0, D10 | ✅ |
| **R16** | 프로덕션 테스트 규율(LLM mock) | A-MEM/langmem 테스트 부재 | eval harness LLM mock | §10 | P1 🔲 |
| **R17** | 자체 eval harness | 공개벤치가 write/forget 안 잼 | 직접 구축 | §10 | P0 🔲 |

## D. 안티-요구 (반면교사로 안 함)

| 안 할 것 | 이유 |
|---|---|
| 기본 그래프 DB(Neo4j) | 운영비/주입(MemOS). 멀티홉 필요 전까지 X |
| freshness LLM 위임 | 체계적 실패 → 결정적 코드 |
| 무한 성장(망각 없음) | A-MEM/mem0 → R5가 해소 |
| fire-and-forget consolidation | Letta/langmem 유실 → durable 큐 |
| SoT↔vector 드리프트 | A-MEM/Letta → 동일 트랜잭션 dual-write |
| 표시 인덱스를 ID로 LLM에 | A-MEM 데이터 손상 → 안정 ID+remap |
| sync/async 코드 중복 | mem0/langmem drift → 단일 구현 |
| 로컬 통합에 불필요한 OAuth/서버 | 로컬은 stdio MCP로 충분(검증) |

---
새 요구 추가 시: **도출 근거 + SPEC 위치** 필수. 결정 필요한 요구는 [`decisions.md`](decisions.md)에 ADR로(R15→D10).
