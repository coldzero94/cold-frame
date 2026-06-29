# Coldframe — P1 Readiness Gaps

> **HISTORICAL (pre-build, RESOLVED).** This was the readiness checklist written *before* P1. Every
> blocker below is closed: the repo is git-initialized + scaffolded, the 7 contract conflicts are
> ratified in code (CLAUDE.md §1 conflict rule), ruff/mypy/pytest/CI/pre-commit are wired, and
> P1–P6 are built (~370 tests green). Kept only as a record of the pre-build state — not current.

# Coldframe — P1 준비도 GAPS (알려진 TODO 외)

**ready_for_p1 판정: ❌ NO.** 문서는 매우 충실하고 아키텍처는 일관되지만, 5개 `docs/build/*.md` 스펙이 서로 독립적으로 작성되어 **TDD가 가장 먼저 닿는 seam에서 상호 모순된 계약**으로 굳어 있다. 7개 blocker를 비준하기 전에는 첫 테스트조차 쓸 수 없다. 각 blocker는 잘못 추측하면 엔진 전체 + eval golden-set 재작업을 강제한다. (저장소 상태 직접 확인: git 미초기화, `cold_frame/`·`tests/` 없음, pyproject에 `[tool.pytest]/[tool.ruff]/[tool.mypy]` 없음, dev extra는 pytest뿐, eval 하버스가 쓰는 `pyyaml` 미등록.)

**핵심 통찰:** 인프라(git init, ruff/mypy/pytest, CI, pre-commit, frontend)는 기계적·저위험이라 추가가 쉽다. **진짜 위험은 문서 모순 — 이는 "결정"이지 누락이 아니다.** 그리고 이 프로젝트의 가치 명제("A-MEM/langmem이 건너뛴, 결정적 LLM-mock 단위테스트")는 LLM ABC와 Status/quarantine 모델이 단일해지기 전엔 빌드 불가능하다.

---

## BLOCKER (P1 시작 전 반드시 비준)

| # | Gap | 권고 |
|---|---|---|
| **B1** | **Sync vs async 모순** — api-contract §0은 전 v1 표면을 동기로 선언(`def add/search`), 그러나 read-and-budget §5.0은 `async def search`, eval §A는 `async def complete` + async mock. README는 동기 호출. 첫 테스트(`test_add_then_search`)와 LLM mock seam이 양립 불가 시그니처를 가짐. | **api-contract §0(sync) 비준** — core는 sync, `to_thread`는 `prompts/mcp.py`에만. read-and-budget §5.0 → `def search`, eval §A → sync `LLM.complete` + sync `ScriptedLLM`로 수정. 1개 소유 문서, 나머지는 포인터. |
| **B2** | **Quarantine 모델이 4가지로 갈림** — (a) `pending`을 4번째 Status 값으로(D21·B4, data-layer §1), (b) api-contract §1도 `pending` Status, (c) eval §C.6은 `quarantine`를 5번째 Status로, (d) read-and-budget §5.2 + prompts §1.4는 Status 3값 유지 + 별도 `held_for_human`/`quarantined` boolean 컬럼 + read-filter. DDL·`Status` Literal·read FILTER·eval YAML 단언이 전부 갈림. | **flag 방식 비준 권장**(`held_for_human INTEGER` + `triage_reason TEXT`, Status = `active\|archived\|deleted`) — provenance trigger와 Triage queue가 이미 flag로 키잉. 5개 문서(SPEC §2/§5, data-layer §1/§1.1, api-contract §1, eval §C.6, read-and-budget §5.2, prompts §1.4) + eval YAML `ExpectNote`를 한 표현으로 sweep. **(직접 확인: api-contract §1은 `pending` Status를, prompts §1.4/read-and-budget §5.2는 flag를 명시적으로 채택 — 실제 모순 확인됨.)** |
| **B3** | **LLM/Embedder ABC가 3가지 시그니처** — eval §A `complete(*, task:TaskTag, system, user, schema, …) -> LLMResult[T]`; api-contract §6 `complete(system, user, *, json_schema:dict, temperature) -> str`; prompts §8 `complete_json(system, user, schema) -> dict`. `task:TaskTag`(mock dispatch·I-LOCAL 강제·구조화 로깅 구동)가 나머지엔 부재. 모든 테스트가 이 seam을 mock함. | **eval §A를 canonical 채택**(task dispatch + Usage + schema), B1대로 sync로 수렴. api-contract §6 + prompts `complete_json`을 `cold_frame/llm/base.py` 한 ABC 포인터로. **`Embedder.embed` 반환형 충돌도 해결** — eval §A `np.ndarray` vs api-contract §5 `list[list[float]]` → **`np.ndarray` 권장**(KNN matmul). (직접 확인됨.) |
| **B4** | **저장소 git 미초기화 + 스캐폴딩 0** — `cold_frame/` 패키지, `tests/`, `.gitignore` 없음. red-green 루프는 commit zero부터 버전관리가 필요. | `git init`; Python `.gitignore`(`__pycache__,*.db,*.db-wal,.venv,dist/,.pytest_cache,.ruff_cache`); SPEC §12 트리를 `__init__.py` stub으로 스캐폴드 + `tests/`; P1 코드 전 초기 커밋. |
| **B5** | **lint/type/test 툴링 없음** — pyproject에 `[tool.pytest.ini_options]`/`[tool.ruff]`/`[tool.mypy]` 없고 dev extra는 `pytest`뿐. 코드베이스가 strict typing(pydantic v2, Protocol/ABC, Literal enum)에 크게 의존 → mypy가 위 모순을 잡아준다. green-bar 정의·CI 게이트 부재. | pyproject에 `[tool.pytest.ini_options]`(markers `slow`,`live`, `addopts="-ra"`), `[tool.ruff]`(E/F/I/UP/B), `[tool.mypy]`(strict) 추가. dev extra 확장: `pytest pytest-cov ruff mypy pyyaml hypothesis`. **`pyyaml`은 eval 하버스의 실제 dep인데 미등록.** |
| **B6** | **Clock/RNG 주입 seam이 코드 계약에 없음** — eval §B.2는 엔진이 `datetime.utcnow()` 직접 호출 금지, `clock.now()`만 호출하고 seeded RNG + `uuid5(case.id:ordinal)` id를 쓴다고 명시. 그러나 api-contract 시그니처가 `clock`/`now`를 일관되게 운반 안 함(`Memory.__init__`에 clock 없음). 사후 주입은 정확히 피하려는 재작업. | `Clock` protocol(`now()->datetime`) + id-factory를 `Memory.__init__`에 추가(기본=시스템 clock+uuid4, eval=`FrozenClock`+uuid5). "어떤 모듈도 `utcnow()`/`uuid4()` 직접 호출 금지, 둘 다 `Memory`→WriteCore→Store로 thread" 규칙을 api-contract에 명시. **P1 첫 커밋에 baked-in.** |
| **B7** | **상수 충돌** — bands(api-contract §4 3밴드 vs data-layer §8/read-and-budget 4밴드), archive floor(0.20 vs 0.10), caps(api-contract `semantic=2000/episodic=500/procedural=100` vs read-and-budget §5.8 `episodic=2000/semantic=5000/procedural=500`). forgetting 테스트는 두 cap 표/두 archive 임계로 작성 불가. | `cold_frame/constants.py`에 한 세트 고정, 모든 문서가 참조. **api-contract §4의 3밴드 + 단일 strength 공식 비준**(직접 확인: evergreen≥0.66/budding/fading<0.33, archive는 `S<0.33 AND archive_score<0.20` OR cap, caps=2000/500/100). REINFORCE_DECAY_INC=0.5, DECAY_S_CAP=365, RRF k=60, FANOUT=4, cosine 0.82/0.93, EMA α=0.1 등 동봉. |

---

## IMPORTANT (P1과 병행 가능하나 곧 필요)

| # | Gap | 권고 |
|---|---|---|
| **I1** | **Provisional 상수 미비준** — HashEmbedder dim=256이 'e.g.'로 hedge됨; HeuristicCounter(`0.75*chars/4+0.25*words`, ±15%)는 B3/D21이 요구하는 보수성(estimate≥actual, `used<=budget`)을 깰 수 있고 CJK를 under-count. dedup eval은 HashEmbedder cosine의 유의미성에 의존. | dim=256 final 확정 + bucketing 알고리즘(blake2b→buckets, L2-norm) 정밀 기술 + 3개 canonical cosine 관계 단위테스트. 토큰 카운터는 conservative(ceil, no down-blend)로 만들거나 eval은 tiktoken·ship은 안전마진임을 문서화. 'frozen constants' 표 1곳. |
| **I2** | **Store ABC가 두 문서에서 갈림** — api-contract §3(`add_note/set_status/knn/bm25/touch/.../purge_note`)와 data-layer §9(`connect/supersede/archive/purge/finish_job/get_meta...`)가 메서드명·`emb` 타입(`list[float]` vs `bytes`) 모두 다름. SQLiteStore는 P1이 가장 먼저 빌드·테스트. | canonical `Store` ABC 1개(두 목록 병합, `reinforce`/`touch` 택1, `emb` 타입은 B3과 일치) → `store/base.py`, data-layer §9는 포인터. |
| **I3** | **Error taxonomy 분산·불완전** — api-contract §2.6(`ColdFrameError` 서브트리)과 eval(`PolicyError`/`EvalError`)이 분리. MCP §7이 에러를 stable code(`invalid_scope/not_found/internal`)로 1:1 매핑해야 하는데 일부 클래스 미정의. | `cold_frame/exceptions.py`에 전 계층 통합(`PolicyError` 포함, parse-failure 처리 정의) + 예외→MCP-code 매핑표 1곳. 저공수, P1 핸들러 테스트 작성 가능해짐. |
| **I4** | **CI/pre-commit 없음** — eval §B.4의 2티어 CI(`tests-core` 머지 게이트=R16/R17, `evals-live` nightly)가 workflow로 존재 안 함. phase 게이트 미강제. | `.github/workflows/ci.yml`(ruff+mypy+`pytest -m "not slow and not live"`, 키·네트워크 0) + `.pre-commit-config.yaml`. phase→suite 맵을 named pytest marker로. |
| **I5** | **Observability/redact 모듈 home 없음** — eval §C.7이 구조화 JSON 로깅 + 'content/PII 절대 미로깅' + `redact_filter` denylist를 강제(보안 통제, 옵션 아님). core deps는 pydantic+numpy뿐이라 structlog 불가. | core는 stdlib `logging` + 커스텀 JSON formatter + `redact_filter`(신규 core dep 없음) → `cold_frame/observability.py`. denylist(`content,text,user,payload,raw,span`) 고정 + `test_logs_have_no_content`를 P1 reliability seed로. |
| **I6** | **Vue UI 번들 빌드/배포 파이프라인 미정·미스캐폴드** — D20/SPEC §9는 prebuilt Vite+Vue3+UnoCSS 정적 번들을 wheel에 동봉 요구. frontend 디렉토리·package.json·asset 위치·hatch force-include·`[ui]` extra 미정. P3 착지지만 패키징 결정은 지금 pyproject·CI에 영향. | P1 blocker 아님. P3 전: `frontend/` 워크스페이스 계획, built-asset 경로(예 `cold_frame/ui/_dist/`), hatch build config, `[ui]` extra 서버 dep, 릴리스 스텝(`pnpm build`→copy→wheel). pnpm/Vite는 릴리스-빌드 전용, user dep 아님. |

---

## NICE (낮은 위험, 보험성)

| # | Gap | 권고 |
|---|---|---|
| **N1** | **Branding indirection·PyPI-name 게이트 미강제** — D19(name=cold-frame) vs D-P2(이름 폐기) 미해결, PyPI/상표 미검증. P1이 `cold-frame`/`~/.cold-frame/`/`cold-frame://`를 하드코딩하면 rename이 위험한 sweep. | `cold_frame/branding.py`에 5개 상수(`PKG,DB_DIR,MCP_ID,URL_SCHEME,UI_PORT=27182`)를 day-one에. 다른 곳 리터럴 금지(lint/grep). CI에 publish 게이트 명시(D19-vs-D-P2 종료 전 `twine upload` 금지). |
| **N2** | **알려진 TODO 잔존** — ux §8.2 strength 공식 reconcile(B7과 겹침; api-contract/read-and-budget은 해결됐으나 ux §8.2/§4.3은 superseded `.42/.30/.18` 잔존), portability-spec.md 부재(export/import bundle·event-log dump 계약 차단). | ux §8.2/§4.3을 canonical strength 섹션 포인터로('superseded' 표기). export/import 전(P2-ish)에 portability/bundle 스펙 작성(`events.ndjson` + notes/edges dump, `event_id` 멱등 import). MED/LOW 리스크는 기존 계획대로 fold. |

---

## 권장 pre-P1 시퀀스

(1) api-contract §0 sync 비준 + LLM ABC·Store ABC 문서 간 수렴 →
(2) quarantine 표현 1개 선택 + 5문서 + eval YAML sweep →
(3) `constants.py`에 상수 동결, bands/caps/thresholds 수렴 →
(4) Clock/RNG/id 주입을 계약에 추가 →
(5) git init + `cold_frame/`·`tests/` 스캐폴드 →
(6) pyproject 툴링(ruff/mypy/pytest markers, dev extra에 pyyaml) →
(7) CI + pre-commit + branding + observability/redact 모듈.
그 후 P1(store + models + add/search + 최소 MCP + eval 하버스)을 settled 계약 위에서 기계적으로 진행.