# cold-frame — 기획 문서 인덱스

> 기획 문서 아카이브. **P1–P6 빌드 완료, v0.1.0 출시됨** — 현재 상태는 루트 `CLAUDE.md`(Status)가
> 단일 출처이고, 여기 문서들은 설계 근거/스펙. 영어 요약: [`DECISIONS.en.md`](DECISIONS.en.md).

## 👉 코딩할 땐 이것만

| 문서 | 용도 |
|---|---|
| **[`SPEC.md`](SPEC.md)** | ⭐ **구현 단일 출처.** 데이터모델·schema·write/read·망각·MCP·CLI/화면·eval·빌드단계. **코딩은 이 문서 하나만 보면 됨.** |
| [`requirements.md`](requirements.md) | 요구 추적: 필요 ← 비교 도출 ← SPEC 위치 (R-번호) |
| [`decisions.md`](decisions.md) | 결정 로그 ADR (D1~D28): 날짜·대안·근거·상태 |

## 배경/근거 (구현 중엔 안 봐도 됨 — `analysis/`)

"왜 이렇게 정했나"의 여정. 추론 사슬 순서:

| # | 문서 | 결론(한 줄) |
|---|---|---|
| 1 | [`analysis/memory-systems-analysis.md`](analysis/memory-systems-analysis.md) | 시장 9종 비교 → 메모리=write/read 문제, 망각·token budget 부재 |
| 2 | [`analysis/build-vs-use-decision.md`](analysis/build-vs-use-decision.md) | 거짓 이분법 → 컴포넌트별 하이브리드 |
| 3 | [`analysis/source-analysis/`](analysis/source-analysis/) | 7종 코드 해부 → 문서≠코드, 어려운 부분 다 비어있음 |
| 4 | [`analysis/blueprint-combine-the-best.md`](analysis/blueprint-combine-the-best.md) | 메커니즘별 베스트 차용 + 우리가 만들 칸 |
| 5 | [`analysis/language-decision.md`](analysis/language-decision.md) | Python+uv (Store 시임, hot path 후이식) |
| 6 | [`analysis/design.md`](analysis/design.md) | 상세 설계 근거(DDL 등) — SPEC이 이걸 통합·압축 |
| 7 | [`ux-design.md`](ux-design.md) | 상세 UX 디자인(리서치·화면 스케치·로드맵) — SPEC §9가 통합 |
| 8 | [`product-strategy.md`](product-strategy.md) | 제품 전략(신뢰·프라이버시·sync·품질·포지셔닝/네이밍) — D15~D19 근거 |

## 추적성 규칙
- 새 요구/기능 → `requirements.md`(도출 근거 + SPEC 위치) 등록.
- 결정 → `decisions.md` ADR(대안·근거·상태). 번복 시 상태만 갱신.
- 구현 디테일/스키마 변경 → `SPEC.md` 본문.

## 참고
- 분석 대상 OSS 클론: `~/chanyoung/memsys-refsrc/` (mem0, graphiti, letta, cognee, MemOS, langmem, A-mem)

## 상태
- ✅ 결정: 전략·저장(SQLite)·패키징(코어/서버 분리)·배포(uv)·언어(Python)·Claude Code 연동(MCP)·노트입자·write·procedural.
- ✅ 채움: 검색·랭킹 세부(SPEC §5), 프로그램 화면(로컬 웹 UI, §9), eval 케이스(§10).
- ✅ **코드 완료: P1–P6 빌드 + v0.1.0 출시** (Homebrew 바이너리 배포, ADR-D28). 현재 상태 단일 출처 = 루트 `CLAUDE.md`(Status).

## 하드닝 / 빌드 스펙 (2026-06-21, P1 직전 심화 — wf_9cebead0)
- [`risks.md`](risks.md) — 리스크 레지스터 (CRITICAL C1~C4 + HIGH/MED/LOW). B1~B7 blocking decisions = ✅ 확정(D21).
- [`security-spec.md`](security-spec.md) — 보안 계약(purge invariant / localhost CSRF / MCP 위협 / import sandbox)
- [`build/prompts.md`](build/prompts.md) · [`build/data-layer.md`](build/data-layer.md) · [`build/read-and-budget.md`](build/read-and-budget.md) · [`build/api-contract.md`](build/api-contract.md) · [`build/eval-and-reliability.md`](build/eval-and-reliability.md) — 미명세 핵심의 구체 빌드 스펙(코딩 시 SPEC와 함께 봄). ⚠️ 이 build/* 스펙은 **빌드 전에 고정**된 것이라 일부는 코드와 드리프트됨 — 비준된 시임(G2~G5, MCP/LLM/UI 표면)은 **코드가 최종**(CLAUDE.md §1). `api-contract.md`/`eval-and-reliability.md` 상단의 SUPERSEDED 배너 참조.

## 코딩 운영 (P1 직전, 2026-06-21)
- [`../CLAUDE.md`](../CLAUDE.md) — **코딩 운영 매뉴얼** (불변식 I1~I17 · TDD 워크플로 · 컨벤션 · 안티패턴 · 빌드순서 · 명령어 · 가드레일). 매 세션 로드됨.
- [`tdd-plan.md`](tdd-plan.md) — **P1 red-green 순서 + G1~G6 인터페이스 게이트(코딩 전 비준)**.
- [`readiness-gaps.md`](readiness-gaps.md) — P1 전 readiness 갭(인프라/스펙 정합 17건).
- `.claude/settings.json` + `.claude/hooks/test-gate.sh` — **테스트-게이트 Stop hook**(코어 테스트 실패 시 턴 종료 차단; 테스트 생기기 전엔 no-op).
