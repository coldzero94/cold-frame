# cold-memo 메모리 코어: MAIN 언어 결정 지원 문서

> 독자: 속도와 안정성을 중시하고, Docker 없는 단순 로컬 설치를 원하며, 추후 제품화 가능성을 열어둔 인프라 엔지니어
> 결정 대상: cold-memo 메모리 **코어**의 주 언어 (멀티테넌트 서버 티어는 이미 분리·후행 결정으로 합의됨)

---

## 1. 핵심 통찰

**이 결정의 가장 큰 오류는 "언어 하나를 고른다"는 프레임 자체다.** 네 개의 옹호 입장은 각자 자기 언어가 코어 전체를 가져가야 한다고 전제하지만, critique가 정확히 짚었듯 현실 세계의 지배적 패턴은 **compiled-engine + scripting-driver**다. 프롬프트가 직접 인용한 프로덕션 벡터 DB인 LanceDB부터가 Rust 엔진을 Python 바인딩으로 소비하며, Cognee는 Rust LanceDB + Kuzu를 Python에서 구동한다. **엔진은 이미 호스트 언어로 작성되지 않는다.**

이 통찰을 cold-memo의 실제 워크로드에 대입하면 세 가지가 분명해진다.

**(1) 이 워크로드에서 호스트 언어 속도는 사용자가 체감하는 지연을 거의 움직이지 않는다.** `add()`의 지배적 비용은 LLM 추출/dedup/conflict 호출(네트워크 + 모델 FLOPs, 수백 ms ~ 수 초), `search()`는 쿼리 임베딩 호출이다. 설계 문서 스스로 "brute-force가 개인 스케일 수천~수만에서 정확·충분(sub-ms ~ low-ms)"이라 명시한다. 즉 end-to-end 지연은 모델 서버와 인덱스 엔진이 결정하지, 오케스트레이션이 Python/Go/Rust/TS인지가 결정하지 않는다. **오케스트레이션을 Rust로 다시 짜는 것은 수 초를 기다리는 경로에서 마이크로초를 깎는 일이다.** 속도 우선순위는 사실상 **라이브러리/아키텍처 선택**("KNN을 절대 인터프리터 루프로 짜지 않는다, 필터·랭킹을 SQL/ANN으로 밀어넣는다")으로 충족되는 것이지, 코어 언어 선택으로 충족되는 것이 아니다.

**(2) 안정성도 대부분 언어가 아니라 엔지니어링 규율의 문제다.** 설계의 landmine 목록(A-MEM index/ID 혼동에 의한 데이터 손상, 조용한 SoT↔vector drift, fire-and-forget consolidation 유실, sync/async 코드 중복 drift)은 거의 전부 **로직·런타임 규율** 실패다. Rust의 `Result`/`Send`/`Sync`는 메모리/동시성 버그 *클래스*를 막지만, critique가 날카롭게 지적했듯 실제로 메모리 스토어를 수년에 걸쳐 부패시키는 버그—잘못된 freshness 해석, 어긋난 `valid_at` 비교, consolidation 워커에서 삼켜진 asyncio 예외—는 타입 시스템이 "부분적으로만 막을 수 있는" 로직 버그다. 즉 **Rust는 이 네트워크-바운드·GC-허용·단일-writer SQLite 워크로드가 거의 생성하지 않는 버그 클래스에 대한 갑옷이고, 정작 데이터를 위협하는 버그 클래스에는 부분적 도움만 준다.** 안정성은 durable jobs 테이블 + 재시도, 단일 트랜잭션 dual-write, 코드 내 deterministic freshness, CI strict typing, 경계 검증으로 확보되며—이것들은 설계가 이미 약속한 항목이고, 어떤 언어도 공짜로 주지 않는다.

**(3) "지금 vs 영원히"는 거짓 이분법이다.** 설계는 로컬 단일 사용자 코어(SQLite/in-process)와 멀티테넌트 서버(별도 FastAPI+Postgres, hard dependency boundary)를 **명시적으로 분리**했다. GIL/동시성 논쟁은 *서버 티어에만* 적용되며, 이는 별도의·후행·독립적으로 언어를 고를 수 있는 컴포넌트다. 따라서 **코어는 반복 속도가 가장 높은 언어로, 멀티테넌트 hot ranking 티어는 나중에 같은 `Store` 인터페이스 뒤에서 컴파일 언어로** 짤 수 있다.

**결론적 리프레임:** 진짜 결정 변수는 엔진 속도가 아니라 **검증된 품질까지의 시간(time-to-validated-quality)**이다. 설계의 Phase 0가 말하듯 가장 크고 오래가는 산출물은 **eval harness**이고, 미해결 제품 질문은 "병목이 retrieval/extraction/forgetting *품질*인가 속도인가"이다. conflict 규칙·decay 곡선·packer 휴리스틱·dedup 임계값을 그 harness에 대고 가장 빨리 반복하게 해주는 언어가 옳은 코어 언어다—그 루프가, inner KNN 루프가 아니라, cold-memo가 좋은지를 결정하기 때문이다.

---

## 2. 언어별 장단점 매트릭스

평가 기준은 cold-memo의 실제 워크로드(네트워크-바운드, 개인 스케일, 컴파일 엔진은 `Store` 뒤로 swappable)다.

| 기준 | Python | Rust | Go | TypeScript |
|---|---|---|---|---|
| **속도 (이 워크로드 기준)** | 충분함. hot 루프는 이미 컴파일 코드(numpy/FTS5-C/sqlite-vec). 인터프리터 세금은 K=10~50 글루에서 무시할 수준. **footgun**: KNN을 파이썬 루프로 짜면 100~1000x 절벽 | 최고이지만 **이 경로에선 사실상 무의미**(네트워크가 2~4 오더 지배). 이득은 product-scale 배치 sweep | 충분함. dedup/RRF/packing 글루에서 CPython 세금 제거. SIMD inner loop는 Rust에 뒤짐 | 충분함(로컬). 단 numeric은 반드시 native로 위임. JS KNN은 ~3~10x 느림 |
| **안정성** | GC-안전하나 컴파일타임 타입 안전 없음 → CI mypy + Pydantic 경계 검증으로 *규율*에 의존. native ext가 crash 면 재유입 | 가장 강함(메모리/동시성 버그 클래스 제거, no-GC tail). 단 freshness/conflict **로직** 버그는 부분만 방어 | 강함. 정적 타입 + GC-안전 + race detector. 장기 stateful 데몬에 적합. enum/sum type 부재로 bi-temporal 로직 boilerplate | 중간. strict 타입은 advisory(any escape), GC. native addon crash 시 프로세스 격리 없음 |
| **ML/LLM 생태계** | **압도적 1위.** 7개 레퍼런스 전부 Python, 모든 임베딩/리랭커/SDK first-class, 로컬 임베딩(sentence-transformers/BGE)이 `pip install` | 약함. candle/ort 존재하나 모델 커버리지·예제 빈약. 레퍼런스 코드 line-for-line 재구현 | 약함. numpy 없음. 공식 Anthropic/OpenAI Go SDK는 있으나 로컬 임베딩은 shell-out/cgo | 중간. agent 프레임워크(LangGraph.js, Vercel AI SDK, MCP)는 TS-native. 로컬 임베딩은 transformers.js/HTTP |
| **단일바이너리 배포** | **최약.** uv/uvx는 dev에 훌륭하나 native single-binary 아님(PyApp/Nuitka는 무겁고 fragile). 설계 §1.3 전체가 이 약점을 우회 | **이상에 가까움.** `cargo build` → 정적 단일 바이너리, rusqlite로 SQLite 내장, cross-compile trivial | **사실상 1위.** `go build` 정적 단일 바이너리, **pure-Go SQLite+FTS5(modernc)로 native dep 0**. 단 cgo SIMD/ANN 가면 forfeit | Bun `--compile`로 좋음. 단 BM25 위해 better-sqlite3(native addon) 강제 → pure-runtime 주장 무효화 |
| **개발 속도** | **최고.** REPL/notebook 반복, 레퍼런스 line-for-line 이식, 최대 contributor pool. 단 velocity가 front-loaded(나중 런타임 버그 비용) | **최저.** borrow checker + async Rust(Pin/lifetime across .await) + trait friction. 2~5x time-to-build, 좁은 pool | 중간-상. 작은 언어 표면, 빠른 컴파일, 인프라 pool. generics는 numeric에 어색 | 높음. 한 언어로 lib+MCP+API. 큰 pool(타깃 유저와 중첩). eval notebook 문화는 Python에 뒤짐 |
| **멀티테넌트 동시성** | 로컬은 비이슈(asyncio + GIL-released I/O). 서버는 GIL → multiprocess(RAM/core 비용↑). **단 서버는 분리된 후행 결정** | 최강(tokio + Send/Sync, GIL 없음). **단 미래 제품에 대한 베팅**, 현재 단일 유저엔 무용 | 강함(goroutine + context). 같은 바이너리가 1→N 테넌트로 Store 어댑터 교체만으로 확장 | 단일 스레드 → CPU-heavy pass가 head-of-line block. worker_threads/native로 offload 필요 |

---

## 3. 언제 무엇이 이기나

### Python
- **이길 때:** 병목이 네트워크-바운드 LLM/임베딩 호출일 때(현 상태). 차별화 가치가 빠르게 바뀌는 분기형 POLICY 로직(conflict/decay/packer/consolidation)을 eval harness에 대고 반복하는 것일 때. 7개 Python 레퍼런스를 line-for-line 이식하고 싶을 때. hot 루프를 vectorized/컴파일 라이브러리에 유지할 규율이 있을 때. 로컬 in-process 임베딩(sovereign offline)이 하드 요구일 때.
- **질 때:** 비개발자에게 진짜 단일 바이너리 배포가 하드 요구일 때. 멀티테넌트 서버가 단일 프로세스 내 CPU-바운드 랭킹으로 지배되고 그걸 한 프로세스에서 스케일하려 할 때. 팀이 strict typing/경계 검증/벡터화 규율이 없을 때. native ext(sqlite-vec/faiss)가 다수 OS/arch에서 mandatory가 될 때.

### Rust
- **이길 때:** 팀이 cold-memo를 내구성 있는 **제품/서비스**로 출하하기로 진짜 커밋했고 velocity 세금을 투자로 받아들일 때. 장기 mutation-heavy 데몬의 안정성이 최우선이고 메모리/동시성 안전이 실제 위협일 때. 비개발자 대상 단일 정적 바이너리가 first-class 하드 요구일 때. 멀티테넌트 미래가 충분히 확실하고 per-tenant CPU/메모리 효율이 비용을 좌우할 때. LanceDB/tantivy를 store substrate로 채택할 때. Python eval 레이어를 PyO3 위에 올릴 의향이 있을 때.
- **질 때(현 상태에 해당):** cold-memo가 단일 유저 로컬 도구로 남고 제품 미래가 안 올 때 → 체감 못하는 속도와 안 쓰는 동시성에 2~4x 빌드 시간 지불. 미해결 문제가 엔진 속도가 아니라 retrieval/extraction/forgetting **품질**일 때(현재 강하게 시사됨). 러닝웨이 압박이 있는 소규모 팀일 때. Phase 0를 아직 안 돌렸을 때 → 가역적 결정을 조기에 hard-code. Rust + async-Rust 깊이가 팀에 없을 때.

### Go
- **이길 때:** 산출물이 "프로그램처럼" 단순 로컬 설치일 때 → pure-Go SQLite+FTS5로 native dep 0, 정적 단일 바이너리가 best-in-class. 장기 stateful 엔진 + durable 백그라운드 워커일 때. 추후 멀티테넌트 서비스로 가고 같은 코드 경로로 1→N 확장하고 싶을 때. heavy compute가 네트워크-바운드로 남을 때. 낮은 장기 유지보수와 인프라/백엔드 pool을 peak numeric 성능보다 중시할 때.
- **질 때:** fully-offline in-process 임베딩이 의존성-가벼운 first-class 기능일 때(Python이 import, Go는 shell-out/cgo). 대규모 벡터 검색 throughput이 실제 병목이고 SIMD/quantization inner loop를 소유하고 싶을 때(Rust/LanceDB가 정답). heavy numeric/ML 실험이 로드맵을 지배할 때. 7개 레퍼런스를 **코드 레벨**로 채굴하는 전략일 때(Go는 copy-paste 레버리지 0).

### TypeScript
- **이길 때:** 주 배포 타깃이 TS 프레임워크(LangGraph.js, Vercel AI SDK, Mastra) 위 agent 개발자이고 zero-IPC in-process 메모리 lib/MCP 서버를 원할 때. 워크로드가 설계대로 네트워크-바운드로 남고 모든 numeric을 native lib에 위임할 때. lib+MCP+제품 API를 한 언어로 가고 싶을 때. richly-linked notes-and-edges 데이터 모델(Rust borrow checker엔 까다로움)에서 반복 속도가 중요할 때.
- **질 때:** 멀티테넌트 제품이 대규모 in-process 벡터/dedup 연산으로 CPU-바운드가 되고 native 위임이 불가할 때. 장기 고동시성 서버 안정성이 하드 게이트일 때. sovereign 고품질 로컬 임베딩이 하드 요구일 때. 팀 무게중심이 Python notebook eval 작업일 때. 코어당 max throughput이 필요할 때.

---

## 4. 추천 + 근거

**추천: (a) Pure Python + uv로 코어를 시작하되, `Store.knn()`/`Store.bm25()` 어댑터 시임을 신성불가침으로 유지하고, 컴파일 hot path는 eval이 병목임을 증명한 *후에만* (e) 점진 이식한다.** 즉 (a)와 (e)의 결합이 정답이고, (d) hybrid는 "지금 PyO3 코어부터"가 아니라 "나중에 정당화된 hot-path-only 이식"의 형태로만 채택한다.

다섯 후보를 명시적으로 검토하면:

**(a) Pure Python + uv — 채택(코어).** 이 워크로드에서 Python의 두 약점(단일 바이너리 배포, GIL)은 둘 다 **현재 코어에 적용되지 않는다.** 배포의 near-term 청중은 개발자이고 `uvx`/`uv tool install`은 frictionless다. GIL은 분리된 후행 서버 티어에만 적용된다. 반면 Python의 두 강점—압도적 ML/LLM 생태계와 최고 개발 속도—는 cold-memo의 실제 차별화 작업(eval harness에 대한 POLICY 반복, 7개 Python 레퍼런스 이식)에 **정확히** 작용한다. 정직한 비용: strict mypy/Pydantic 경계 검증, 절대 KNN을 파이썬 루프로 안 짜는 규율, asyncio durable jobs 규율—모두 설계가 이미 mandate한 항목이다.

**(b) Pure Rust — 기각(현 상태).** Rust-first는 네트워크-바운드·GC-허용·단일-writer 워크로드에 대한 over-engineering이다. 거의 없는 버그 클래스를 갑옷으로 막고, 정작 메모리를 부패시키는 freshness/conflict 로직 버그는 부분만 막는다. 속도 이득은 옹호자 본인이 "사용자가 체감하는 수준에선 신기루"라 인정한다. velocity 세금(2~5x)을 Phase 0가 엔진 속도가 병목인지 확인하기도 전에 지불하게 된다. Rust가 진짜 강한 카드—비개발자 단일 바이너리, 멀티테넌트 CPU-바운드 티어—는 둘 다 *지금 짓는 코어가 아닌* 분리·후행 컴포넌트에 있다.

**(c) Pure Go — 강력한 차점, 조건부.** Go는 "단순 로컬 설치"라는 명시적 우선순위에 **가장 깨끗한** 답이다(pure-Go SQLite+FTS5 = native dep 0, 정적 단일 바이너리). 장기 데몬 안정성도 Python보다 정직하게 강하다. 만약 (1) 비개발자 대상 단일 바이너리 배포가 *near-term 하드 요구*이고, (2) 7개 레퍼런스를 코드가 아닌 *알고리즘*으로만 채굴할 각오가 되어 있으며, (3) fully-offline in-process 임베딩이 코어가 아니라면 → **Go가 정답이 된다.** 기각하지 않고 1순위 대안으로 둔다. 비용은 정직하다: numpy 부재, 레퍼런스 코드 레벨 레버리지 0, cgo로 SIMD/ANN 가는 순간 배포 superpower forfeit.

**(d) Hybrid (compiled core + bindings) — "지금 코어부터"는 기각, "나중에 hot-path만"은 채택.** PyO3/maturin 코어 + Python eval 레이어는 Rust 옹호자 본인이 "best of both"라 부르지만, critique가 옳게 지적했듯 이것은 *오히려 Rust-first를 하지 말라는 가장 강한 논거*다—policy/eval 레이어가 Python을 원함을 인정하고, 현재 hot하지도 않은 경로를 위해 상시 FFI 경계(새 버그 클래스 + 빌드 복잡도)를 추가하기 때문이다. hybrid는 **upfront full rewrite가 아니라 eval로 정당화된 deferred hot-path 이식**으로만 가치가 있다.

**(e) Python 시작 후 hot path 이식 — 채택(경로).** 시작 언어를 정한다고 컴파일 hot path가 막히지 않는다. 가역적 결정을 미룰 뿐이다. KNN/MinHash-LSH/RRF가 대규모 후보셋에서 실제 병목임을 eval이 증명하면 그때 PyO3/napi/WASM 또는 그냥 더 빠른 컴파일 ANN 인덱스로 옮긴다.

**정직한 근거 요약:** 사용자가 명명한 두 우선순위(속도·안정성)는 이 워크로드에서 호스트 언어가 거의 움직이지 못한다—속도는 라이브러리/아키텍처 선택, 안정성은 엔지니어링 규율의 문제다. Go/Rust가 outright 이기는 유일한 축은 "비개발자 단일 바이너리"인데, 이는 near-term 청중(개발자)에게 *아직 하드 요구가 아니다*. 따라서 코어는 반복 속도와 레퍼런스 마찰을 최소화하는 언어—**오늘은 Python**—로 짓고, 엔진을 swappable하게 유지하며, 컴파일 hot path는 eval로 정당화된 후행 결정으로 둔다.

---

## 5. 이행 경로

이 권고는 이미 결정된 **SQLite-first + decoupled-server** 설계와 정합적이며, 그 설계가 mandate한 시임을 그대로 활용한다.

**지금 작성할 것:**
1. **Eval harness 먼저(Phase 0).** 'cold'의 정의, path-independent 평가셋, conflict/decay/dedup/packer를 mock LLM 뒤에서 결정론적으로 단위 테스트하는 골든 테스트. 이것이 언어와 무관한 최대·최장수 산출물이며, "품질 vs 속도 중 무엇이 병목인가"를 답한다.
2. **`Store` 추상화를 코어의 헌법으로.** `Store.knn()` / `Store.bm25()` / `Store.upsert()`를 추상 인터페이스로 고정. 기본 구현은 **pure-numpy KNN + SQLite FTS5 BM25**(native ext 0, 모든 플랫폼에서 robust한 BASE 설치). sqlite-vec/faiss/LanceDB는 optional extra 뒤로 게이팅.
3. **POLICY 코어를 Python으로.** bi-temporal conflict resolution, deterministic freshness(max-over-serial), token-budget packer, decay/consolidation 엔진—7개 레퍼런스에서 직접 이식. Pydantic v2 모델을 모든 경계에, strict mypy/pyright를 CI에.
4. **안정성 규율을 코드로 못박기.** durable jobs 테이블 + 재시도(fire-and-forget 금지), notes+FTS+vec+edges를 단일 SQLite 트랜잭션 dual-write, asyncio 태스크 예외를 절대 삼키지 않는 워커 래퍼.
5. **배포는 dev-first.** `cold-memo` 엔트리포인트 + extras를 pyproject에. `uvx cold-memo` / `uv tool install`을 1급 경로로. 최소 코어(pydantic + numpy)로 의존성 표면 작게.

**미룰 것:**
- **멀티테넌트 서버 티어 전체.** `cold-memo[server]`(FastAPI + Postgres + pgvector)는 hard dependency boundary 뒤 분리 컴포넌트. 그 언어/동시성 모델은 *독립적·후행* 결정—코어 언어를 좌우하지 않는다.
- **컴파일 hot path.** eval이 in-process CPU-바운드 랭킹이 실제 병목임을 증명하기 전까지 PyO3/napi/cgo/WASM 이식 보류.
- **비개발자 단일 바이너리.** PyApp/Nuitka는 그 청중이 *실제* near-term 요구가 될 때만. 그 전까지는 CI 복잡도를 들이지 않는다.

**고통스러운 재작성을 피하는 법:**
- **시임을 신성하게.** 모든 성능 민감 in-process 작업(벡터 ANN, BM25)은 호스트 언어와 무관하게 컴파일 인덱스 엔진(sqlite-vec→pgvector HNSW, LanceDB, tantivy) 소유. 호스트 언어는 오케스트레이션 + 정책만. 이 어댑터 경계를 보존하는 것이 단 하나의 결정적 아키텍처 커밋이다.
- **footgun 규율.** KNN을 절대 파이썬 루프로 안 짠다(설계의 anti-pattern 규칙). 필터/랭킹을 SQL/ANN으로 밀어넣는다. 이것만 지키면 Python 속도는 비이슈로 남는다.
- **call convention을 좁고 안정적으로.** 코어의 공개 API(`add`/`search`/`Store` 메서드)를 작고 직교하게 유지하면, 나중에 hot path를 컴파일 컴포넌트로 빼거나 서버 티어를 다른 언어로 짤 때 데이터 모델/호출 규약이 ossify되지 않는다. critique가 경고한 "staged migration이 말은 싸고 실행은 비싼" 함정을 이 좁은 경계가 완화한다.

**기존 결정과의 상호작용:** SQLite-first는 Python(stdlib sqlite3 + FTS5 내장)과 가장 마찰이 적고, 단일 파일 모델은 crash recovery/백업이 trivial하며 dual-write 트랜잭션 안정성을 그대로 얻는다. decoupled-server 결정은 정확히 GIL 논쟁을 코어에서 분리해내므로, Python을 코어로 선택해도 미래 제품 티어의 동시성 모델을 전혀 제약하지 않는다. 두 기존 결정 모두 이 권고를 강화한다.

**무엇보다: 언어 판정이 Phase 0를 선점하게 하지 마라.** 'cold'를 정의하고, eval harness를 짓고, 품질이 병목인지 엔진 속도가 병목인지 먼저 확인하라. 엔진/언어 선택은 설계 문서 자신의 표현대로 **싸고 가역적인 LATER 결정**이며, 지금 커밋하는 것은 그것을 알려줄 싼 실험들보다 앞서 커밋하는 것이다.