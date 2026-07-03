# cold-memo: LLM/에이전트 메모리 시스템 비교 분석 및 BUILD vs USE 의사결정 보고서

> ⚠️ HISTORICAL analysis (2026-06, pre-SPEC, "cold-memo" working name) — superseded by SPEC.md + code (CLAUDE.md §1).

> 대상 독자: vessl.ai에서 greenfield 프로젝트 "cold-memo"를 시작하는 인프라 엔지니어
> 작성 기준일: 2026-06-21. 모든 수치/주장은 제공된 검증(verification `checks`) 결과에 따라 교정·표기했습니다.

---

## 1. TL;DR & 한눈에 보기

### 결론 요약 (Verdict)

2026년 시점에서 에이전트 메모리는 **"저장소" 문제가 아니라 "쓰기 경로(write path)와 읽기 경로(read path)" 문제**입니다. 저장소(vector/graph/relational)는 이미 상품화되었고, 진짜 어려운 부분은 (a) 추출/중복제거/충돌해결(write), (b) 검색 정밀도 + 랭킹 + 토큰 예산(read), (c) 망각/consolidation, (d) 멀티테넌트 스코핑입니다. 표준 벤치마크(LoCoMo/LongMemEval/BEAM)는 이 중 write/forget 단계를 **거의 측정하지 않으므로**, 벤더 수치는 회의적으로 봐야 합니다.

cold-memo가 "cold(archival/tiered) 메모리"를 핵심 차별점으로 삼는다면, 대부분의 기성 시스템은 **자동 망각/티어링/콜드 아카이브를 1급 기능으로 제공하지 않습니다**(Mem0, Letta, Zep, Cognee, A-MEM 모두 자동 decay/TTL 부재 또는 수동). 따라서 **"메모리 엔진은 빌려 쓰고, 티어링/콜드 정책은 직접 만든다"는 하이브리드 전략**이 가장 합리적입니다. 자세한 권고는 6절.

### 비교 매트릭스

| 시스템 | 분류 | 메모리 타입 | 저장소 | 추출 방식 | 검색/랭킹 | 충돌 해결 | 망각 | 배포 모델 | 라이선스 | 성숙도 |
|---|---|---|---|---|---|---|---|---|---|---|
| **Mem0** | 전용 라이브러리 + SaaS | 시맨틱/팩트 중심, 에피소딕, 엔티티 | Vector DB(~20종, Qdrant 기본) + 내장 엔티티 | LLM 추출. 논문=2-phase(ADD/UPDATE/DELETE/NOOP), **현행 코드=single-pass ADD-only** | 현행=하이브리드(semantic+BM25+entity 융합) | 버전에 따라 다름. ADD-only는 누적 위주(테스트 필수) | **자동 없음**, scope 만료(run_id) 수동 | OSS lib + self-host + SaaS | Apache-2.0 + 상용 | 성숙(~59k★, ECAI 2025, v2.0.x) |
| **Letta/MemGPT** | 프레임워크 내장(stateful agent server) | core(in-context)/recall/archival, working | Postgres+pgvector / SQLite | **에이전트 self-edit**(tool call) + sleep-time agent 백그라운드 | 티어별. archival=semantic+tag(+hybrid/RRF, 현행 문서화 추정) | LLM in-line 덮어쓰기, archival 자동 dedup 없음 | **자동 없음**. char limit + FIFO 요약 eviction | self-host server + Letta Cloud | Apache-2.0 + 상용 | ~23.4k★, v0.16.x, seed-stage |
| **Zep/Graphiti** | OSS lib(Graphiti) + SaaS(Zep) | 에피소딕/시맨틱/community, **bi-temporal** | Neo4j/FalkorDB/Neptune + 임베딩 | LLM 다단계(entity+reflexion+resolution+temporal) | **하이브리드**(semantic+BM25+graph) + 다중 reranker(RRF/MMR/cross-encoder) | **bi-temporal edge invalidation**(소프트, 이력 보존) | **자동 없음**. invalidation=비삭제, retention은 개발자 책임 | Graphiti self-host + Zep Cloud(BYOC) | Graphiti Apache-2.0 / Zep 상용 | ~27.7k★, v0.29.x, YC W24 |
| **Cognee** | OSS lib + self-host + SaaS | 시맨틱(KG)/에피소딕/working/temporal | Graph(Kuzu 기본)+Vector(LanceDB)+SQLite | **ECL 파이프라인**(add/cognify/memify), 6단계 LLM 추출 | 14+ search type(GRAPH_COMPLETION 기본=vector hint+graph traversal) | **자동 없음**. DataPoint 버전/upsert 수동 | **자동 decay 없음**. forget(item/dataset/user), memify는 블로그 수준 주장 | pip + Cloud + 1-click 템플릿 | Apache-2.0 | ~18.2k★, v1.1.x, $7.5M seed |
| **LangMem + LangGraph Store** | 라이브러리 + 프레임워크 프리미티브 | 시맨틱(collections/profiles)/절차적/에피소딕(개념만) | LangGraph BaseStore(InMemory/Postgres/Redis/Mongo) | hot-path 도구 + 백그라운드 manager(LLM) | store.search(semantic+filter), IndexConfig | LLM manager가 consolidate/delete, vector-level 자동 dedup 없음 | LangMem 자체 없음. **store 레벨 TTLConfig**(default_ttl/refresh_on_read/sweep) | self-host(LangGraph) + Platform | MIT(LangMem/LangGraph), Platform 상용 | **미성숙**(0.0.x, ~1.5k★, 74 릴리스) |
| **A-MEM** | 연구 OSS lib | 에피소딕+시맨틱(단일 진화 노트망) | ChromaDB + all-MiniLM-L6-v2 | **Zettelkasten 노트**(LLM 생성: keyword/tag/context/link) | top-k 코사인(reranker 없음) | **memory evolution**(이웃 속성 재작성), 명시적 reconciliation 없음 | **없음**(수동 delete만) | self-host lib(in-process) | MIT | 연구(NeurIPS 2025 poster, ~1.1k★) |
| **MemOS** | lib + self-host + SaaS | **plaintext/activation(KV-cache)/parametric(LoRA)** | Neo4j+Qdrant+Redis(+SQLite/PolarDB) | MemReader 파싱 + LLM 그래프 추출 | 하이브리드(vector+FTS+graph), KV-cache 주입 | **lifecycle states**(Generated→...→Expired) + Merged, 버전/롤백 | **명시적**: Lifespan Policy(TTL/decay), Archived→Expired | pip + Docker + (K8s 미확인) + Cloud | Apache-2.0 + 상용 | ~9.9k★, v2.0.x, 39인 컨소시엄 |
| **연구 계보**(GenAgents→MemoryBank→SCM→MemoChat→RecallM→SeCom) | 연구(패턴) | working/에피소딕/시맨틱/temporal | 각 논문별(FAISS/Neo4j/ChromaDB 등) | 시스템별 상이 | recency+importance+relevance 스코어(GenAgents 기준) | 대부분 append. **RecallM만 belief-update**로 retract | **MemoryBank=Ebbinghaus R=e^(−t/S)**, GenAgents=0.995 decay | self-host 연구 코드만 | 대부분 MIT/Apache | 연구급 |
| **플랫폼/프레임워크 내장**(OpenAI/Anthropic/Gemini; LlamaIndex/CrewAI/AutoGen/LangGraph) | 플랫폼·프레임워크 내장 | type-agnostic(관례적) | 벤더 관리 또는 자체 인프라(Anthropic memory tool) | 대부분 **자동 추출 없음**(소비자 앱·LlamaIndex fact block 예외) | 프레임워크별. Anthropic memory tool은 **랭킹 없음**(모델이 파일 선택) | **대체로 부재**(단, 현행 CrewAI는 unified Memory로 자동 consolidation 제공) | 대체로 부재. OpenAI Response 30일 TTL, Conversations 무기한 | 관리형 SaaS / 자체 인프라 / 라이브러리 | 플랫폼=독점, 프레임워크=MIT/Apache | 대형 벤더 백업 |
| **Build-Your-Own(티어/콜드 패턴)** | 연구/프레임워크 패턴 합성 | working/에피소딕/시맨틱/절차적/**archival(cold)** | Vector+Graph+Relational+KV+Object(cold) | LLM 2-phase가 주류 | 하이브리드+rerank+token budgeting, async write | 3방식(overwrite/bi-temporal/**deterministic code**) | 부분적(decay/consolidation/tier/TTL/invalidation) | 조합형 self-host | 의존성별 | 신생-실재 카테고리 |

---

## 2. 카테고리/택소노미

### 2.1 그룹핑

- **전용 라이브러리/서비스 (Dedicated memory layer)**: Mem0, Zep/Graphiti, Cognee, A-MEM, MemOS. 에이전트 프레임워크와 무관하게 "메모리"만 담당하는 컴포넌트(일부는 SaaS 동반).
- **프레임워크 내장 (Framework-native)**: Letta/MemGPT(메모리 내장 에이전트 런타임), LangMem(LangGraph BaseStore 위 SDK). 메모리가 에이전트 루프/스토어와 결합.
- **플랫폼 내장 (Platform-native)**: OpenAI(ChatGPT memory/Dreaming, Conversations API), Anthropic(memory tool, context editing/compaction, CLAUDE.md), Google Gemini(Personal Context). "공짜로 얻는 베이스라인".
- **연구 (Research lineage)**: Generative Agents→MemoryBank→SCM→MemoChat→RecallM→SeCom, A-MEM(NeurIPS 2025), MemOS/Mem0/Zep 논문. 차용할 설계 패턴의 출처.

### 2.2 실제로 중요한 선택 축 (Axes that matter)

1. **쓰기 경로(write path)**: 자동 추출 여부, 중복제거, **충돌/freshness 해결**. 가장 어렵고 가장 적게 벤치마크됨.
2. **읽기 경로(read path)**: 하이브리드 검색 여부, **reranker 존재**, token budgeting. "vector similarity는 맞는 후보를 잘못된 순서로 반환한다"(Mem0 2026 블로그).
3. **충돌/시간 모델**: overwrite(Mem0) vs bi-temporal invalidation(Zep) vs deterministic code(연구). 감사/시점 정확성이 필요하면 후자 둘.
4. **망각/consolidation/티어링**: 자동 decay/TTL/콜드 아카이브 — cold-memo의 핵심 축. **거의 모든 기성 시스템이 약함.**
5. **스코핑/멀티테넌시**: user/agent/session/org 격리, ACL. 대부분 application-layer 책임이며 벤치마크 미흡(cross-tenant 누출 리스크).
6. **배포/라이선스/성숙도/운영 부담**: self-host 가능 여부, Apache/MIT 여부, 그래프 DB 운영 비용, bus-factor.
7. **메모리의 물리적 형태**: 텍스트/벡터(대다수) vs **KV-cache·parametric**(MemOS 단독) — 추론 지연(TTFT) 절감이 목표라면 후자.

---

## 3. 시스템별 심층 분석

### 3.1 Mem0
- **메커니즘**: LLM 기반 추출이 핵심. **단, 논문(arXiv:2504.19413, ECAI 2025)과 2026 코드가 다름**. 논문=2-phase(extract → update tool call로 ADD/UPDATE/DELETE/NOOP, m=10/s=10, GPT-4o-mini). 현행 코드=**single-pass ADD-only**(기본 LLM gpt-5-mini, embedder text-embedding-3-small). 검색은 현행 하이브리드(semantic+BM25+entity 융합).
- **강점**: 강력한 peer-reviewed LoCoMo 결과, 큰 토큰/지연 절감(논문 ~7k vs 26k tokens, p95 latency 91%↓), 백엔드/프로바이더 무관(~20 vector DB), Apache-2.0, 명확한 멀티레벨 스코핑, 큰 커뮤니티(낮은 bus-factor).
- **약점/검증 caveat**:
  - **외부 graph DB 지원 제거됨**(Neo4j/Memgraph/Neptune/Kuzu/AGE). 현행은 내장 엔티티 링킹으로 랭킹에만 반영 — 직접 traversal 불가(벤더 스스로 "regression"이라 표기). 그래프 추론이 필요하면 주의.
  - ADD-only 누적은 stale/모순 메모리 위험 — **배포 버전에서 UPDATE/DELETE 동작을 반드시 테스트**.
  - 자동 decay/consolidation 없음(수동).
  - **버전 문자열 교정**: 프로필의 "v1.0.x"는 오류, **현행 v2.0.x(v2.0.7, 2026-06-17)**.
  - 채택사(Netflix/Lemonade/Rocket Money)는 1차 출처 미확인(**uncertain**), Qwen 임베더 권고도 문서 미확인(**uncertain**).
  - 벤치마크 수치가 페이지/버전 간 흔들림(LoCoMo 91.6 README vs 92.5 research; LongMemEval 94.8 vs 94.4) — 정확 수치는 보수적으로.
- **Best-for**: per-user 영속 개인화, support/sales 에이전트, 풀히스토리 stuffing이 비싼 챗 에이전트, drop-in SDK를 원하는 팀.

### 3.2 Letta/MemGPT
- **메커니즘**: OS 비유의 stateful agent server. **에이전트가 직접** core memory block(시스템 프롬프트에 항상 고정)을 self-edit(tool call)하고, recall(대화 이력), archival(벡터)로 페이징. **sleep-time agent**가 N스텝(기본 5)마다 백그라운드로 "learned context"를 재작성.
- **강점**: 메모리가 1급·검사 가능 객체(ADE로 실제 context window를 눈으로 확인 — 인프라팀에 큰 디버깅 이점), 에이전트 self-edit, 강한 연구 계보(DMR/sleep-time 수치 검증됨), Apache-2.0 self-host(Postgres/pgvector) + Cloud, `.af` 포터빌리티.
- **약점/caveat**:
  - **얇은 메모리 라이브러리가 아니라 의견이 강한 풀 에이전트 런타임** — 이미 자체 에이전트 프레임워크가 있다면 무거움.
  - 자동 forgetting/decay 없음, archival cross-record dedup/merge 없음(누적).
  - agentic 검색은 모델이 올바른 도구를 호출해야 함(게으른/오프롬프트 모델은 recall 실패).
  - Cloud는 임베딩 모델 고정(text-embedding-3-small).
  - **검증 업데이트**: 프로필은 hybrid/RRF가 1차 문서 미확인이라 했으나, **현행 Letta 문서는 hybrid semantic+full-text + RRF(rrf_score)를 문서화한 것으로 보임** — 이 hedge는 outdated일 가능성. $70M post-money는 TechCrunch 출처(PR Newswire 아님).
- **Best-for**: 다세션 companion/assistant, 투명·디버깅 가능한 메모리를 원하는 팀, 장문 문서 페이징, lock-in 없는 OSS 에이전트+메모리 서버.

### 3.3 Zep/Graphiti
- **메커니즘**: temporal knowledge graph. Graphiti가 episode를 점진적 ingest하여 entity+fact-edge로 추출(entity extraction n=4, reflexion, BGE-m3 1024-dim, all-caps relation_type, predefined Cypher). 검색=semantic+BM25(Lucene)+graph traversal, 다중 reranker(RRF/MMR/episode-mentions/node-distance/cross-encoder).
- **핵심 차별점 = bi-temporal 모델**: 이벤트 시간 T와 ingestion 시간 T'를 분리, edge당 4개 타임스탬프. 충돌 시 **invalidate(t_invalid 설정), 삭제하지 않음** → point-in-time/감사 쿼리 가능.
- **강점**: 진짜 bi-temporal(대부분 단일 timeline만), 이력 보존 충돌 처리, 점진적 실시간 ingest, read path에 LLM 요약 없음(저지연), Apache-2.0 실엔진, non-lossy provenance, Pydantic ontology, OSS→Cloud 마이그레이션 경로.
- **약점/caveat**:
  - **write path가 LLM 호출 다수**(extraction+reflexion+resolution+fact+temporal+contradiction) → 대량 ingest 비용/지연 큼.
  - **그래프 DB 운영 필수**(Neo4j/FalkorDB/Neptune).
  - 자동 forgetting/TTL 없음 — 그래프 무한 성장, retention은 개발자 책임.
  - **벤치마크 caveat 중요**: DMR은 작고 saturated(저자 스스로 "부적절"), LongMemEval에서 **single-session-assistant는 오히려 하락**(gpt-4o −17.7%, gpt-4o-mini −9.06%). 모든 수치 벤더 self-report, MemGPT는 LongMemEval 직접 비교 실패. "18.5%/15.2%"는 상대 개선 표현(절대 포인트는 +11.0/+8.4).
  - 펀딩 시그널 약함(third-party ~$2.3M total, 저신뢰). "Context Graph Engine"은 프로필 자작 명칭(문서는 "Context Graph"/"Context Lake").
- **Best-for**: 시점 정확성/감사가 필요한 진화하는 per-user 메모리(CRM/support/regulated), group_id 멀티테넌트 SaaS, 비정형+구조화 데이터 융합.

### 3.4 Cognee
- **메커니즘**: ECL(Extract, Cognify, Load) 파이프라인 — `add`(38+ 포맷, dlt 기반), `cognify`(6단계 LLM 추출), `memify`(피드백 기반 후처리). DataPoint(UUID로 vector/graph/relational 연결)가 단위. v1.0은 remember/recall/improve/forget 4-verb API. 검색=14+ SearchType(기본 GRAPH_COMPLETION=vector hint+graph traversal).
- **강점**: hybrid graph+vector+relational 단일 엔진(멀티홉 추론), 타입드 DataPoint+ontology, 매우 넓은 백엔드/완전 로컬 가능, dlt 기반 production ingest, 풍부한 검색 모드, Apache-2.0+활발한 펀딩($7.5M seed).
- **약점/caveat**:
  - 자동 충돌 해결/temporal decay 없음(버전/upsert/forget 수동).
  - `cognify`가 LLM-heavy(ingest 비용/지연 높음), 운영 복잡(graph+vector+relational).
  - 벤치마크 thin: 24 HotpotQA 문항, 경쟁사 정확 수치 미공개, evaluator 변동성 인정. 매우 큰 % 개선(+1618% EM)은 작은 분모 기반 — 통계적으로 취약.
  - 문서 in-flux(4-verb가 legacy building block 위에 layering).
  - **검증 교정**: "ECL"은 현행 core 문서에 없음(legacy 브랜딩). memify의 pruning/usage-reweighting는 **블로그 수준 주장**(기술 문서는 더 보수적). 백엔드 매트릭스 부정확(graph에 FalkorDB/NetworkX는 현행 미표기; vector에 DuckDB 없음/Turbopuffer 추가). Bayer "10,000 papers"는 미확인 마케팅.
- **Best-for**: 멀티홉/관계형 추론(연구/정책/컴플라이언스), 전체 스택 self-host, 타입드·검사 가능 메모리, dlt 파이프라인.

### 3.5 LangMem (+ LangGraph BaseStore)
- **메커니즘**: 시맨틱(collections vs profiles)/절차적(프롬프트 최적화)/에피소딕(개념만) 모델. hot-path 도구 + 백그라운드 manager(debounced ReflectionExecutor). 검색은 LangGraph store에 위임(namespace tuple + IndexConfig semantic + filter). 망각은 한 계층 아래 **TTLConfig**(default_ttl/refresh_on_read/sweep_interval_minutes).
- **강점**: 깔끔한 인지 모델, **절차적 메모리/프롬프트 최적화가 진짜 차별점**(에이전트가 자기 시스템 프롬프트를 재작성), 동기 도구 + 비동기 백그라운드, pluggable Postgres/Redis/Mongo, MIT + LangChain 생태계.
- **약점/caveat**:
  - **매우 미성숙**(0.0.x, ~1.5k★, API churn 리스크). 에피소딕은 도구 부재(직접 구현). LangMem 자체엔 decay 없음(store TTL만, salience 기반 아님).
  - 충돌/consolidation은 LLM 기반(비용/지연/비결정성). "any framework"라지만 사실상 LangGraph 결합.
  - 공개 벤치마크 없음. 스코핑은 namespace 관례(강제 ACL 아님).
  - **검증 교정**: "52 releases"는 오류 → **74 releases**(PyPI). per-store TTL 지원(InMemory/Postgres) 1차 미확인. GitHub watchers 12는 subscribers_count(API quirk).
- **Best-for**: 이미 LangGraph 표준화된 팀, 자기개선 instruction 에이전트, 빠른 프로토타이핑.

### 3.6 A-MEM
- **메커니즘**: Zettelkasten — 각 상호작용을 atomic note(content/keyword/tag/context/embedding/link)로 LLM이 작성. 추가 시 top-k 이웃을 찾아 link 결정 + **이웃 속성 재작성(memory evolution)**. 검색=top-k 코사인(all-MiniLM-L6-v2, reranker 없음).
- **강점**: links 기반 설계의 citable 레퍼런스 구현(NeurIPS 2025 poster, MIT), 토큰 효율(~1,200–2,500 vs 16,900), 멀티홉 강점(GPT-4o-mini Multi-Hop F1 45.85 vs MemGPT 25.52), prompt 기반이라 검사/커스터마이즈 용이.
- **약점/caveat**:
  - **forgetting/scoping 전무**(멀티테넌시 직접 구현), write가 LLM-heavy(매 add마다 이웃 검색+LLM), top-k 코사인+소형 임베더+reranker 없음, 연구 코드 성숙도(README 스스로 "논문 재현은 별도 repo").
  - **검증 교정**: 프로필의 "Average F1 across categories"(27.02/26.65/25.02 등)는 **single-hop F1을 잘못 라벨링**한 것(refuted). 정성적 결론(강한 모델에선 작은 마진, 멀티홉/약한 모델에서 큰 이득)은 유지됨. "bidirectional links"는 논문 미확인.
- **Best-for**: links/graph 메모리를 **차용(adapt)**하려는 팀, 토큰 빠듯한 다세션 챗, 연구/ablation.

### 3.7 MemOS
- **메커니즘**: "memory OS". MemCube(스케줄 가능 단위)가 **plaintext / activation(KV-cache) / parametric(LoRA)** 세 타입을 통합. 3-layer(Interface/Operation/Infrastructure). MemReader 추출, MemScheduler 랭킹(contextual similarity+access frequency+temporal decay+priority tags), KV-cache를 attention에 직접 주입.
- **강점**: **KV-cache·parametric 메모리를 1급 스케줄 단위로 모델링하는 거의 유일한 시스템** → 추론 시점 지연(TTFT) 실측 이득(최대 94.2% 절감, 출력 동일성 검증). 거버넌스/lifecycle 풍부(TTL/decay/ACL/provenance/audit/버전+롤백), 동일 backbone(GPT-4o-mini)+H800 동일 하드웨어 벤치 방법론, Apache-2.0.
- **약점/caveat**:
  - **대규모 surface/높은 복잡도**(Neo4j+Qdrant+Redis 의존), v2.0.x churn 잦음.
  - parametric/KV 파이프라인 OSS 성숙도가 논문 비전에 뒤짐(type-shifting은 약속에 가까움 — uncertain).
  - 일부 헤드라인(+43.70%, 72% lower tokens, +2568%)은 **v2 논문 테이블이 아닌 product/README 주장** — 외부 인용 시 그렇게 표기.
  - 중국 생태계(Qwen/DashScope/MiniMax/PolarDB) 결합, 일부 문서 중국어.
  - **검증 교정**: v2.0은 더 이상 "Preview"가 아닌 production("Stardust"). K8s(v2.0.11+) 1차 미확인. **PyPI "MemoryOS"는 별개 EMNLP-2025 프로젝트(BAI-LAB/MemoryOS)와 namesake 충돌** — 패키지 혼동 주의.
- **Best-for**: 추론 시점 저지연 메모리 주입(KV-cache 재사용)이 필요한 장문 다턴 에이전트, 거버넌스가 필요한 엔터프라이즈, 참조 아키텍처(MemCube+3-layer)를 원하고 Neo4j/Qdrant/Redis self-host 가능한 인프라팀.

### 3.8 연구 계보 (Generative Agents → ... → SeCom)
차용할 **설계 패턴의 원전**. (검증으로 수치 거의 verbatim 확인)
- **Generative Agents**: `score = recency + importance + relevance`(α 모두 1), recency exp decay 0.995, importance 1–10, reflection threshold 150. min-max 정규화. — 사실상 모든 production 시스템의 베이스라인.
- **MemoryBank**: Ebbinghaus **R = e^(−t/S)**(S 초기 1, recall마다 +1) — 원리적·저렴한 decay/reinforcement.
- **RecallM**: belief updating + temporal reasoning(graph+temporal index). 지식 업데이트에서 vector DB 대비 "4배" 효과(단, 일반 QA는 vector DB보다 낮음). **검증 교정**: "LangChain orchestrated"는 **refuted**(논문에 없음), Cisco Research 소속(순수 학계 아님).
- **SeCom**: topic-segment 단위 + LLMLingua-2 압축(75%, xlm-roberta-large) → 압축이 denoiser로 작용해 recall↑. ICLR 2025. LoCoMo GPT4Score 71.57(BM25) vs turn-level 65.58 등 검증.
- **MemoChat**: memorization-retrieval-response, instruction-tuned.
- **seCall 규명**: 7절 참조.

### 3.9 플랫폼/프레임워크 내장 (베이스라인)
- **Anthropic memory tool**: **client-side/개발자 소유 스토어**(/memories 가상 디렉터리, create/str_replace/insert/delete/view/rename), **랭킹 없음**(모델이 파일 선택), **ZDR 적격** → 규제/프라이빗 환경 적합. context editing(server-side, 100k 트리거/3 tool use 유지, clear_tool_uses_20250919) + compaction. 벤치: memory+editing **+39%**, editing alone **+29%**, 100-turn에서 토큰 **84%↓**(모두 Anthropic 내부 평가).
- **OpenAI dev**: conversation state는 **cross-conversation 메모리 아님**(replay/chain, 모든 토큰 input 과금). 자동 추출 없음. Response 30일 TTL / Conversations 무기한. **Assistants API는 2026-08-26 sunset**(후속 Responses+Conversations+AgentKit).
- **ChatGPT/Gemini**(소비자): 자동 추출/consolidation 무료(Dreaming, Personal Context)지만 **불투명·개발자 통제 불가**, app memory layer로 사용 불가.
- **프레임워크**: LangGraph Store(put/search, 자동 추출 opt-in, TTL 없음), LlamaIndex(token_limit 30000 등, fact/vector block), AutoGen Memory protocol(명시 add). 
- **검증 핵심 교정**: 프로필의 **CrewAI 서술은 stale**. 현행 CrewAI는 **unified Memory class(LanceDB 기본 + 자동 fact 추출 + LLM keep/update/delete/insert consolidation + 지수 recency decay(recency_half_life_days) + forget())**를 제공 → "충돌 해결/망각 부재"의 예시로 CrewAI를 들면 안 됨. AutoGen은 ~Q1 2026 maintenance mode(후속 Microsoft Agent Framework), AG2가 community 계승.

---

## 4. 핵심 메커니즘 비교 (Design decisions)

### 4.1 Write path (extraction / dedup / conflict resolution)
- **자동 추출 유무**: Mem0/Zep/Cognee/A-MEM/MemOS = LLM 자동 추출. Letta = 에이전트 self-edit + sleep-time. LangMem = opt-in. **플랫폼 dev API(OpenAI/Anthropic)는 자동 추출 없음**(소비자 앱·LlamaIndex fact block 예외).
- **충돌 해결 3대 접근**:
  1. **Operation-based overwrite (Mem0)**: 간단하지만 "변경=대체"로 이력 손실. 현행 ADD-only는 누적 위주.
  2. **Bi-temporal non-destructive invalidation (Zep/Graphiti)**: 이력 보존 + 시점 쿼리. 운영 복잡도↑.
  3. **Deterministic code-based (연구, arXiv:2606.01435)**: LLM은 freshness 추적에 체계적으로 실패(prior-override, serial-comparison drift "64K 75% → 262K 61%"). 처방=BM25 retrieval + LLM이 버전드 후보 추출 + **~50줄 Python max(serial)** → FC-SH gpt-4o-mini 78.0% / gpt-4o 94.8%(vs long-context 60.0%). **시사점: 어느 시스템을 쓰든 "무엇이 최신인가"를 LLM에 맡기지 말 것.**
- **MemOS만의 형태**: lifecycle state(Merged) + parametric distillation, plaintext→activation→parametric type-shifting.

### 4.2 Read path (retrieval + ranking + token budgeting)
- **하이브리드가 표준**: Mem0(현행 semantic+BM25+entity 융합), Zep(semantic+BM25+graph + 다중 reranker), Cognee(14+ 모드), MemOS(vector+FTS+graph+KV 주입).
- **reranker 유무가 정밀도를 가름**: Zep은 RRF/MMR/cross-encoder 명시. **A-MEM은 reranker 없음(top-k 코사인)** — 정밀도 ceiling. Anthropic memory tool은 **랭킹 자체가 없음**.
- **token budgeting**: 모두 top-k를 고정 토큰 cap에 맞춰 선택. Letta/MemGPT는 disk↔RAM 페이징으로 모델링. 연구계는 deterministic post-retrieval assembly(코드 기반 max-over-version) 권장.

### 4.3 Forgetting / consolidation
- **명시적 자동 망각**: **MemOS(TTL/decay/Archived→Expired)**, **현행 CrewAI(recency_half_life_days + forget())**, MemoryBank(Ebbinghaus, 연구), Generative Agents(0.995 decay + reflection consolidation).
- **부재/수동**: Mem0, Letta, Zep(invalidation=비삭제이지 decay 아님), Cognee(forget 수동), A-MEM(전무), LangMem 자체(store TTL만).
- **공통 미해결: staleness** — "직장 정보처럼 자주 검색되던 메모리가 이직 시점에 confidently wrong이 됨." 명시적 temporal 모델 없이는 자동 invalidation 불가.

### 4.4 Scoping
- 멀티축(user/agent/session-run/app-org + metadata)이 production norm(Mem0/Letta/Zep group_id/LangGraph namespace).
- **A-MEM은 스코핑 1급 부재**, 플랫폼/프레임워크는 대체로 application-layer 책임. 안정적 user_id 가정이 익명/멀티디바이스/혼합 auth에서 깨짐 — **cross-tenant 누출 리스크 + 벤치마크 미흡**.

---

## 5. 평가/벤치마크

- **LoCoMo** (Maharana 2024): 다세션 대화 QA. 10 대화, ~600 dialogue/~26k tokens, ~200 Q. 카테고리 = single-hop/multi-hop/temporal/**commonsense·world knowledge**(프로필의 "open-domain"은 교정)/adversarial(제외). **주의**: Zep 저자 스스로 "DMR/소형 벤치는 현대 LLM 컨텍스트에 들어가 saturated → 메모리 평가에 부적절"이라 명시. 대화당 ~60 messages.
- **LongMemEval** (Wu, ICLR 2025): 500 문항, 상용/long-context가 ~30% 정확도 하락, 5능력(information extraction/multi-session/temporal/knowledge update/abstention). LongMemEval_S ~115k tokens/~50 sessions(프로필 ~40은 교정), _M ~500 sessions.
- **DMR** (MemGPT): MemGPT+GPT-4 92.5%/0.814 vs base 32.1% 등(검증됨). 단 작고 saturated.
- **BEAM** (arXiv:2510.27246, ICLR 2026): 100 대화·최대 10M tokens·2,000 probing Q, 1M/10M 트랙. 구조화 메모리가 long-context 대비 +3.5~12.7% — **10M 윈도우만으로는 부족**. ("1M→10M ~25% 손실"은 부분 uncertain.)

**벤더 수치를 얼마나 믿을 것인가**:
- 거의 모두 **벤더 self-report**, harness 의존, 독립 재현 부족. Mem0 README/research 페이지 간 수치 불일치(91.6 vs 92.5), Mem0 "67.13%"는 **single-hop**(overall 66.88%) — 라벨 교정.
- **refuted/uncertain로 표기해야 할 것**: Cognee 경쟁사 수치 미공개·+1618% 같은 작은 분모 기반 수치; Zep single-session-assistant **하락**; "Zep 63.8% vs Mem0 49.0% LongMemEval"은 **temporal 서브태스크**를 overall로 오인한 것(벤더 간 논쟁); OpenAI Dreaming 수치는 1차 페이지 403·내부 평가·방법론 미공개; Anthropic 39/29/84%는 내부 평가.
- **핵심**: write/extraction과 forgetting/consolidation을 **직접 측정하는 공개 벤치마크가 사실상 없다**. 즉 좋은 retrieval 수치 뒤에 실제 실패(stale/모순/누출)가 숨을 수 있다 → cold-memo는 자체 eval harness가 필요.

---

## 6. cold-memo를 위한 의사결정 프레임 (BUILD vs USE)

### 6.1 의사결정 트리

1. **그냥 기성 메모리를 채택(USE)하라 — 다음이면**:
   - 단일 플랫폼(Anthropic/OpenAI)에 이미 커밋했고 다세션 에이전트가 "지금" 필요 → **Anthropic memory tool + context editing + compaction(스토어는 자체 인프라)** 이 강력한 "아직 직접 만들지 마라" 기본값. (ZDR·데이터 소유 보너스)
   - per-user 개인화·support/sales recall, 풀히스토리 stuffing 회피가 목적 → **Mem0**(drop-in, Apache-2.0, 큰 커뮤니티).
   - **시점 정확성/감사/temporal**이 핵심 → **Zep/Graphiti**(bi-temporal). 단 그래프 DB 운영 + write 비용 감수.
   - 이미 LangGraph 표준화 → **LangMem**(미성숙 감안).

2. **얇은 wrapper면 충분 — 다음이면**:
   - 기성 엔진(예: Mem0/Zep)의 write/read는 쓰되, **스코핑·콜드 티어링·freshness 정책만 자체 레이어로 감싼다**. cold-memo의 "cold" 요구는 대부분 여기에 해당.

3. **직접 빌드(BUILD)가 정당화 — 다음이면**:
   - **콜드/티어드 메모리가 1급 제품 차별점**이고 기성 시스템의 자동 망각/아카이브 부재가 blocker일 때.
   - 멀티테넌트 격리/컴플라이언스를 엔진 레벨에서 강제해야 할 때.
   - **deterministic freshness 해결**을 핵심으로 삼아 LLM-judged overwrite를 신뢰하지 않을 때.
   - 인프라 회사로서 메모리 자체가 제품/플랫폼일 때(vessl.ai의 포지셔닝과 부합).

### 6.2 'cold-memo'를 위한 tiered/cold 메모리 관점
프로젝트명이 시사하듯 **티어드 메모리 + cold(archival) 계층**이 핵심. 현실은 **거의 모든 기성 시스템이 콜드 아카이브/자동 티어링을 1급으로 제공하지 않음**:
- Mem0/Letta/Zep/Cognee/A-MEM = 자동 decay/TTL 없음 또는 수동. 무한 성장.
- 예외적 참조: **MemGPT/Letta의 virtual-context paging**(disk=cold ↔ RAM=hot, ~70% context-pressure 트리거)이 콜드 패러다임의 가장 가까운 레퍼런스. **MemOS의 lifecycle(Archived→MemVault cold storage→Expired)** + Lifespan Policy(TTL/decay). **MemoryBank의 R=e^(−t/S)**가 hot/warm/cold 승격·강등 신호로 적합.
- cold tier 물리 저장: **object/blob storage**(압축·드물게 접근) + cold vector index. hot=KV/세션 버퍼, warm=vector DB(검색), cold=blob+lazy-load.

### 6.3 권장 레퍼런스 아키텍처 (BUILD 또는 thin-wrapper 시)

```
[Async Write Pipeline] (요청 경로 밖)
  ingest → LLM 2-phase extract (Mem0식 EXTRACT) 
         → embedding-similarity dedup 
         → 충돌해결: bi-temporal invalidation(Zep식) + deterministic max(serial)(연구식)  ← LLM에 freshness 위임 금지
         → tier 배치(hot/warm/cold) + scope keys(user/agent/run/org + metadata)

[Storage Tiers]
  hot   : KV/cache (세션 버퍼, MemGPT식 core block)
  warm  : Vector DB (Qdrant/pgvector) — 일상 검색
  cold  : Object storage + cold vector index — lazy paging (cold-memo 핵심)
  (옵션) graph: Neo4j/FalkorDB — temporal/multi-hop이 필요할 때만

[Read Path]
  metadata filter(scope) → hybrid 검색(semantic+BM25+entity/graph) 
                         → reranker(cross-encoder/Cohere) 
                         → token budgeting(top-k under cap)
                         → deterministic assembly(max-over-version)

[Forgetting/Consolidation] (백그라운드, sleep-time식)
  recency/utility decay(MemoryBank R=e^(−t/S)) → consolidation(에피소딕→시맨틱 요약 후 cold 강등) 
  → bi-temporal invalidation(soft forget, provenance 유지) → TTL/expiry(cold reclaim)

[Eval Harness]  ← 기성 벤치가 안 재는 write/forget을 자체 측정
```

**어느 아이디어를 어디서 차용할지**:
- **추출 2-phase + 멀티레벨 스코핑** → Mem0.
- **티어드 virtual-context 페이징(hot/cold), sleep-time consolidation** → Letta/MemGPT.
- **bi-temporal invalidation + provenance + 하이브리드+다중 reranker** → Zep/Graphiti.
- **decay 공식(R=e^(−t/S)), recency+importance+relevance 스코어, reflection consolidation** → MemoryBank / Generative Agents.
- **deterministic freshness(BM25 + 버전 추출 + max(serial))** → arXiv:2606.01435. (LLM에 최신성 위임 금지)
- **KV-cache/parametric 티어 + lifecycle/governance** → MemOS(추론 지연이 목표이거나 거버넌스 필요 시).
- **segment+압축 retrieval 정밀도** → SeCom. **links/노트 진화** → A-MEM.

---

## 7. seCall 규명

**'seCall'은 가장 유력하게 SeCom**(arXiv:2502.05589, ICLR 2025, "On Memory Construction and Retrieval for Personalized Conversational Agents", Microsoft Research)을 가리킵니다. 어떤 시스템도 문자 그대로 "seCall"이라 명명되지 않으며, 이는 거의 확실히 오타/garble입니다. SeCom은 연구 계보의 가장 최근·잘 엔지니어링된 anchor 시스템으로 음성·의미적으로 가장 가까운 매치입니다. **대안 해석**: (a) RecallM("recall" 어근), (b) 일반적 의미의 "recall(메모리 회상)". 신뢰도: **낮음~중간(low-to-medium)** — 1차 출처로 확증 불가하며 best-guess입니다.

---

## 8. 다음 단계 제안 (체크리스트)

1. **요구사항 확정**: cold-memo의 "cold"가 (a) 비용 최적화 archival인지, (b) 시점 정확성/감사인지, (c) 추론 지연(KV-cache)인지 명시 — 이에 따라 차용 시스템이 갈림.
2. **2주 스파이크**: Mem0(v2.0.x) self-host에서 **배포 버전의 UPDATE/DELETE/충돌 동작을 실제로 테스트**(ADD-only 누적 여부 확인). 병행으로 Zep/Graphiti의 bi-temporal invalidation을 동일 데이터로 비교.
3. **freshness PoC**: arXiv:2606.01435의 BM25 + 버전 추출 + ~50줄 max(serial)를 재현해 LLM-judged overwrite와 정확도 비교(FC-SH/FC-MH).
4. **자체 eval harness 구축**: LoCoMo/LongMemEval뿐 아니라 **write/extraction 품질, dedup, forgetting, cross-tenant 누출**을 직접 측정하는 케이스 추가(공개 벤치는 이를 안 잼).
5. **티어 아키텍처 프로토타입**: hot(KV)/warm(Qdrant or pgvector)/cold(object storage + lazy vector) + MemoryBank식 decay 승격·강등. MemGPT 페이징을 레퍼런스로.
6. **스코핑/프라이버시 설계 우선**: user/agent/run/org + metadata 격리, 안정적 user_id 부재(익명/멀티디바이스) 대비책, ZDR이 필요하면 Anthropic memory tool 패턴(스토어 자체 인프라) 채택.
7. **운영 비용 검토**: graph DB(Neo4j/FalkorDB) 필요성 재평가 — temporal/multi-hop이 없으면 vector-only로 시작. write가 LLM-heavy이므로 async write 기본화.
8. **벤더 수치 보정 가이드 내부 공유**: 본 보고서의 refuted/uncertain 항목(Mem0 single-hop 라벨, Zep self-assistant 하락, Cognee 경쟁사 수치 미공개, OpenAI Dreaming 내부평가, CrewAI 현행 unified Memory)을 의사결정 문서에 명시.
9. **라이선스 확인**: 차용 코드(특히 연구 repo)의 실제 LICENSE 확인. 핵심 후보(Mem0/Letta/Graphiti = Apache-2.0)는 안전.

---

### 검증 기반 핵심 교정 요약 (참고)
- Mem0 현행은 **v2.0.x**(프로필 v1.0.x 오류), 외부 graph DB **제거**, single-pass ADD-only.
- Letta: 현행 문서에 **hybrid+RRF 문서화**(과거 hedge outdated).
- Zep: single-session-assistant **하락**, 모든 수치 self-report, "Context Graph Engine"은 자작 명칭.
- Cognee: "ECL"·memify 자기개선은 블로그 수준, 백엔드 매트릭스 부정확.
- LangMem: **74 releases**(52 오류), 매우 미성숙.
- A-MEM: "Average F1" 라벨은 실제 **single-hop F1**(refuted).
- MemOS: v2.0은 production(Preview 아님), PyPI namesake 충돌, K8s 미확인.
- 연구계: RecallM "LangChain orchestrated" **refuted**.
- 플랫폼/프레임워크: **현행 CrewAI는 자동 consolidation+decay+forget() 제공**(프로필 stale), AutoGen maintenance mode.