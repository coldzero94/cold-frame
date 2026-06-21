# cold-memo: BUILD / USE / HYBRID 의사결정 지원 문서

> 본 문서는 정답을 처방하지 않는다. 세 advocacy 입장과 한 건의 adversarial critique를 종합해, "우리"가 스스로 결정할 수 있도록 트레이드오프를 정직하게 정리하는 것이 목적이다.

---

## 1. 핵심 프레이밍 — 이 결정은 단일 이분법이 아니다

세 입장 모두 BUILD / USE / HYBRID를 하나의 전역 스위치처럼 다루지만, critique가 정확히 지적했듯 **그 프레이밍 자체가 거짓 이분법**이다. 결정을 정직하게 다시 세우면 다음과 같다.

**(1) 결정은 단일 노브가 아니라 컴포넌트별(per-component)이다.**
메모리 시스템은 최소 6개 축으로 분해된다: `storage / write-extraction / conflict-resolution / read-retrieval / forgetting-tiering / scoping`.
- storage(Qdrant/pgvector/object store), reranker, BM25 라이브러리는 **세 입장 모두 이미 USE한다.** 즉 "순수 BUILD"는 존재하지 않는다.
- "HYBRID"는 정의상 컴포넌트별 혼합이다.
따라서 실제 질문은 "전체를 살까 만들까"가 아니라 **"각 축을 어디에 둘 것인가"**이다.

**(2) HYBRID는 제3의 선택지가 아니라 스펙트럼이다.**
- thin-wrapper(a)는 운영상 USE에 가깝다.
- hard-fork(b)는 BUILD + 영구 머지세(merge tax)에 가깝다.
- 두 sub-variant은 BUILD와 USE 사이 거리보다 **서로 더 멀다.** 그래서 "HYBRID"라는 단일 라벨도 거짓 통합이다.

**(3) "소유"는 이진값이 아니라 벡터다.**
소유 대상은 최소 6종: 차별화 정책 코드 / write·read 내부 / 데이터 모델·스키마 / 운영 런타임 / 로드맵 / 법적 포크 권한. 경로마다 축별 점수가 다르다. 사용자의 "우리 것" 제약이 **무엇을 뜻하는지 미정의**라서 입장들이 서로 다른 정의를 두고 논쟁 중이다 (5절·4절에서 분해).

**(4) 결정을 좌우하는 두 질문이 아직 미해결이다.**
- **'cold'의 정의가 미정.** (a) 비용 최적화 아카이벌, (b) point-in-time/audit 정확도, (c) inference-latency KV-cache 재사용 — 셋의 정답 substrate가 완전히 다르다(각각 S3+lazy-load 배관 / Graphiti bi-temporal / MemOS). 세 입장 모두 "cold = tiered archival"을 기정사실로 가정하고 답하지만, 그 가정 자체가 검증되지 않았다.
- **eval harness가 아직 없다.** 세 입장 모두 harness가 필수·경로무관(decision-independent)·최대 규모 산출물임을 인정한다. 그렇다면 **엔진 선택은 상대적으로 싸고 되돌릴 수 있는 나중 결정**이다.

**결론적 프레이밍:** 지금 전역 verdict를 내리라는 요구는 *값싸고 결정적인 실험을 돌리기도 전에 커밋하라는 요구*다. 이 문서는 verdict를 내리지 않고, 트레이드오프와 — 무엇보다 — **결정 전에 답해야 할 것**을 드러낸다.

---

## 2. 세 경로 장단점 매트릭스

| 경로 | 소유·커스터마이즈 적합도 | 초기 노력 | 유지보수 부담 | 락인/드리프트 위험 | 품질 도달 속도 | 통제력 |
|---|---|---|---|---|---|---|
| **BUILD** | ✅ 구조적 완전 소유 | ❌ 분기 단위(quarters) | ❌ 상시 팀 커밋 | ✅ 업스트림 없음 | ⚠️ retrieval 정밀도는 긴 꼬리 | ✅ 전 축 통제 |
| **USE** | ⚠️ 차별화층만 소유, 내부는 임대 | ✅ 주 단위(weeks) | ⚠️ 외부화되나 재검증 상시 | ❌ 데이터모델+로드맵 락인 | ✅ 검증된 파이프라인 즉시 | ❌ write/freshness는 엔진 소유 |
| **HYBRID (thin-wrap)** | ✅ 차별화층 100% / 커밋티 임대 | ✅ 2–4주 | ⚠️ 핀+재검증+크로스심 디버깅 | ⚠️ seam 미검증·업스트림 의존 | ✅ 빠름(차별화는 별도) | ⚠️ write 내부는 못 고침 |
| **HYBRID (fork)** | ✅ 법적·코드 전체 소유 | ⚠️ 헤드스타트 후 분기 단위 | ❌ 비선형 rebase/머지세 | ⚠️ 포크 드리프트 | ⚠️ 헤드스타트 있으나 분기 | ✅ 전 축 통제(분기 후) |

> 주의: 표의 "초기 노력"은 모두 **commodity 층** 기준이다. 어느 경로든 cold-tiering 차별화층과 eval harness는 별도로 지어야 하며, 이 부분은 경로 간 차이가 거의 없다.

---

## 3. 경로별 심층 장단점

### 3.1 BUILD — commodity storage/LLM API 위에 write·read·forgetting·scoping을 직접 작성

**장점**
- 계약이 아닌 **구조적 소유**: 업스트림 로드맵/리팩터/relicensing이 엔진을 흔들 수 없다. (Mem0가 external graph-DB 지원을 제거하고 single-pass ADD-only로 선회한 — 벤더 스스로 regression이라 부른 — 사례가 경고담.)
- 차별화 요소와 소유 제약이 정확히 일치: 자동 망각/consolidation/cold-archival은 *어떤* off-the-shelf 엔진도 first-class로 제공하지 않으므로, 엔진을 소유하는 것이 이를 first-class로 만드는 유일한 길.
- deterministic·debuggable·golden-test 가능한 write/forget 경로. LLM-heavy write path(Zep의 ingest당 다중 호출, Cognee의 6단계 cognify)의 비결정성/불투명성을 회피.

**단점**
- mem0/zep/letta가 messy real data로 이미 디버깅한 엣지케이스(extraction 예외, dedup 임계값, multi-tenant 페이지네이션, async write의 retry/idempotency)를 다시 발견하게 된다.
- time-to-first-value가 `pip install mem0`보다 명백히 느리다 → 정치적으로 비싸다.
- 상시 유지보수·eval가 일회성이 아닌 standing cost. 인력이 흔들리면 손수 만든 엔진이 커뮤니티 엔진보다 빨리 썩는다(Mem0 ~59k stars의 낮은 bus-factor 대비).

**숨은 비용**
- eval harness가 사실상 엔진보다 큰 진짜 프로젝트다 (단, **경로무관 비용** — USE에서도 발생하므로 BUILD에 불리하게 작용하지 않음).
- async write 파이프라인의 분산시스템 표면(큐/retry/idempotency/dedup race/backpressure/"write 성공·consolidation 실패" 부분실패).
- cold-tier paging의 tail-latency cliff와 cache-coherence(warm copy가 cold promote 후에도 authoritative한가). cold-memo에서 **가장 신규이고 가장 검증 안 된** 부분.
- embedding-model 백도어 락인(수백만 임베딩 후 모델 교체 = 비싼 재임베딩 마이그레이션).

**advocate가 과대판매한 지점 (critique 반영)**
- ❗ **"freshness fix는 ~50줄, decay는 한 줄"** — 패킷 전체에서 가장 oversold된 주장. 50줄은 max(serial) 리졸버 *뿐*이며, 이미 올바른 versioned extraction(LLM 단계, 논문도 결정적이지 않음), 엔티티 타입별 깔끔한 serial/version 필드, partial update·모순 소스 간 merge semantics를 전제한다. BUILD 자신의 cons도 *"version/serial extraction을 엔티티 타입에 걸쳐 올바르게 정의하는 곳에 진짜 버그가 산다"*고 인정한다 → 헤드라인 pro가 자기 con과 모순. 진짜 어려움은 **스키마와 extraction contract**이지 50줄 max()가 아니다. decay도 R=e^(-t/S)는 산수이고, 어려움은 S 보정·recall 증가 정의·t 기준·promotion/demotion·consolidation 상호작용이다.
- ❗ **"deterministic이 LLM judging을 이긴다(94.8% vs 60%)"** — 이는 raw long-context stuffing과의 비교이지, 잘 만든 LLM-judged write path와의 비교가 아니다. 결정적 결과로 밀반입된 strawman. 94.8%도 여전히 LLM extraction 위에 올라가 있다.

**언제 이게 정답인가**
- cold/tiered/archival 메모리 또는 deterministic freshness가 *실제 제품 차별화*이고, **write/read 경로 자체도 소유**해야 할 때(freshness 결정성 + write 단위테스트).
- engine-level 멀티테넌트 격리/컴플라이언스가 엔진에서 강제·증명되어야 할 때.
- retrieval+분산시스템 역량 있는 소규모 senior 팀 + 상시 eval/유지보수 커밋이 가능할 때.

**언제 피해야 하나**
- 몇 주 내 작동하는 메모리가 필요하거나 메모리가 사이드 피처일 때.
- 진짜 필요가 graph/multi-hop 또는 point-in-time audit이면 Graphiti(Apache-2.0)를 reinventing하게 됨.
- 소유가 *유일한* 동인이고 write/read 경로 소유는 불필요할 때 → thin-wrapper HYBRID가 훨씬 싸게 동일 소유 제공 → 풀 BUILD는 over-spend.

---

### 3.2 USE — 성숙한 OSS 엔진(기본 Mem0; temporal/audit이면 Zep/Graphiti; 풀 런타임이면 Letta)을 버전 고정 의존성으로 채택, 그 위에 cold-memo 차별화 로직만 구축

**장점**
- write path(LLM extraction, dedup)와 read path(hybrid semantic+BM25+entity/graph, reranking, token budgeting) — 느리게 굳는 어려운 부분이 이미 존재·검증.
- storage 백엔드 optionality 무료(Mem0 ~20 vector DB 추상화).
- 전 후보 Apache-2.0/MIT → 법적으로 포크 가능, 되돌릴 수 있는 베팅.
- 큰 커뮤니티로 bus-factor·유지보수 외부화(보안 패치, embedding 마이그레이션, 드라이버 churn).

**단점**
- ❗ **헤드라인 차별화(자동 망각/consolidation/cold-archival)는 어느 엔진도 제공 안 함** → USE-as-is는 핵심 기능을 *전달하지 못한다.* "v1 in weeks"는 **소유할 필요 없던 것의 v1**이다. USE는 "파이프라인 임대 + 차별화 구축"으로 정직하게 팔아야지 "채택해서 출시"가 아니다.
- 바꿀 수 없는 opinionated 내부: Mem0는 single-pass ADD-only·external graph 제거; Letta는 thin lib가 아닌 무거운 agent 런타임.
- freshness가 cold-memo 기준엔 틀린 방식(LLM-judged overwrite). write path 안에 살아서 포크 없이는 못 고침.
- 데이터 모델 락인(API 아닌 schema 차원). 마이그레이션 = extractor/transformer 작성.

**숨은 비용 / advocate 과대판매 (critique 반영)**
- ❗ **"production hardened — AWS/Netflix/Zep 30x/Letta가 쓴다"** — 이 hardening 주장은 source 분석 doc 스스로 **UNVERIFIED**로 표시한 adopter 일화에 기댄다(Mem0 adopter 리스트 "1차 출처 미확인", Zep traffic/funding "low confidence"). brief가 신뢰하지 말라던 vendor 자가보고 social proof를 load-bearing 논거로 재활용 중.
- ❗ **"cold-tier 차별화는 엔진 내부를 건드리지 않고 add()/search()/delete() 위에 깔끔히 얹힌다"** — 주장될 뿐 입증된 적 없다. HYBRID가 자기 hidden_costs에서 이를 파괴한다: *"진짜 forgetting/consolidation은 엔진 자체 store의 레코드(vector rows, entity links)를 mutate/demote/tombstone 해야 하며, public API로는 종종 불가능."* 누구도 plugin/extension seam의 존재를 검증하지 않았다.
- 'It just works'는 함정: eval harness는 여전히 직접 지어야 함. version-pinning은 필수이며 매 업그레이드가 재검증 프로젝트. "언제든 포크" 탈출구는 생각보다 비쌈(큰 미숙지 코드베이스 + 모든 향후 패치 인수).

**언제 이게 정답인가**
- 작동하는 멀티세션 메모리가 곧 필요 + 팀 소규모, time-to-value가 지배할 때.
- 차별화가 엔진 public API *위* 정책층으로 표현 가능할 때(seam 존재 검증 전제).
- 유지보수 외부화를 가치 있게 보고 버전 고정+재검증을 감수할 때.

**언제 피해야 하나**
- freshness/conflict-resolution이 deterministic이어야 하고 LLM-judged overwrite가 허용 불가일 때(엔진 write path 내부 → 포크 강제).
- engine-level 멀티테넌트 격리가 hard requirement일 때.
- 메모리 엔진 *자체*가 vessl.ai가 팔/라이선스할 제품일 때 → 핵심 commodity 임대가 전략적 부채.

---

### 3.3 HYBRID — Apache-2.0 엔진을 확장: commodity write/read/storage는 빌려오고 차별화(cold tiering + deterministic forgetting/freshness)는 직접 소유. 기본 (a) clean seam의 thin wrapper, 명명된 trigger에서만 (b) hard fork로 escalate

**장점**
- 차별화에만 엔지니어링 집중, commodity는 재유도하지 않음(이론상).
- 소유층이 substrate 무관하게 진짜 소유됨: tiering 정책, decay 곡선, hot/warm/cold lazy-paging, max-over-serial 리졸버가 엔진 *위*에 산다.
- deterministic freshness를 wrapper 경계에서 강제 가능(어떤 엔진 위에서도 read/write 인터셉트).
- 빠른 v1 + 되돌릴 수 있음(전 후보 Apache-2.0/MIT, 인터페이스 뒤에서 엔진 교체 가능).
- 정직한 소유 스토리: "차별화 소유 + commodity 임대."

**단점 / advocate 과대판매 (critique 반영)**
- ❗ **"엔지니어링 100%를 차별화에, 0%를 commodity에"** — 수사적으로 깔끔하나 운영상 거짓. 자기 hidden_costs가 인정: version-pinning 경계, 크로스심 디버깅, re-extraction double-spend, 매 업그레이드 substrate 재검증, substrate 고르기 *전에* eval harness 구축. commodity를 통합·모니터·디버그·재검증하는 것 자체가 상시 commodity 작업 — 직접 만든 작은 경로보다 *총 오버헤드가 더 클 수도* 있다(설계 안 한 추상화를 가로질러 디버깅하므로).
- ❗ **"seam quality로 substrate 고르고 Mem0가 가장 깔끔한 thin-wrap 대상"** — 자기 cons가 고백: *"검색으로 Mem0/Graphiti의 plugin/extension API를 찾지 못함; 유일한 seam이 internal monkey-patching이면 (a)가 조용히 (b)로 degrade."* 즉 중심 추천이 **검증 안 된 가정(clean seam 존재)** 위에 서 있다. 전략 전체가 의존하는 단 하나를 아무도 확인하지 않았다.
- partial 소유는 실재: (a)에서 write/read 경로는 소유하지 못함. Mem0의 ADD-only가 stale 사실을 내면 read 시점 mitigate는 되나 extraction 자체는 포크 없이 못 고침.
- fork drift(b)는 비선형 비용. churny한 업스트림(Mem0 v2.0.x)에서 cherry-pick/rebase 부담이 기하급수적으로.

**숨은 비용**
- "thin" wrapper는 forgetting이 엔진 store를 건드리는 순간 thin이 아니게 됨 → public API로 불가능 → 내부로 손 뻗어 조용히 soft-fork(부분 소유 + honest fork의 깔끔한 분기 이점 없음).
- re-extraction double-spend(엔진 extraction LLM 비용 + 자체 versioned extraction).
- 멀티테넌트 leakage 상속: application-layer convention 위에 scoping을 볼트온하면 *거짓 격리* — leak은 소유하나 코드는 안 썼다.

**언제 이게 정답인가**
- write/read를 인터셉트할 clean seam이 존재(또는 구축 가능)하여 unmodified 엔진 위에 tiering/forgetting/freshness가 살 수 있을 때 → 값싼 (a) 유지.
- 차별화가 정확히 tiering/forgetting/freshness 정책이고 novel extraction/retrieval 알고리즘이 *아닐* 때.
- eval harness를 일찍 세워 substrate를 측정값으로 고를 수 있을 때.

**언제 피해야 하나**
- forgetting이 엔진 내부 store mutate 없이는 구현 불가 + public API 부재 → (a)가 조용히 soft-fork. 차라리 (b) 또는 BUILD를 명시적으로.
- 차별화가 알고보니 write/read 경로 자체에 있을 때 → 소유하고 싶던 층에서 엔진 opinion과 싸움.
- engine-level 격리가 hard requirement인데 substrate가 scoping을 app-layer로 다룰 때.

---

## 4. '우리 것으로 만들 수 있는가' 관점

"소유"는 이진값이 아니라 **6차원 벡터**다 (critique). 사용자의 "우리 것" 제약이 무엇을 뜻하는지부터 분해해야 경로 비교가 의미를 가진다.

| 소유 차원 | BUILD | USE | HYBRID (a) | HYBRID (b) |
|---|---|---|---|---|
| 차별화 정책 코드(tiering/forget/freshness) | ✅ | ✅ | ✅ | ✅ |
| write/read 내부(extraction/conflict-res) | ✅ | ❌ (엔진) | ❌ (엔진) | ✅ (분기 후) |
| 데이터 모델/스키마 | ✅ | ❌ (락인) | ⚠️ (얽힘) | ✅ |
| 운영 런타임 | ✅ | ⚠️ | ⚠️ | ✅ |
| 로드맵 통제 | ✅ | ❌ | ❌ | ✅ |
| 법적 포크 권한 | n/a | ✅ | ✅ | ✅ (행사) |

**"소유한다"가 실제로 요구하는 것:**
- **차별화 정책의 소유**라면: USE·HYBRID·BUILD 모두 만족(이 코드는 어느 경로든 net-new, 우리 것). 이 정의에선 BUILD의 소유 우위가 USE 대비 좁고, HYBRID(a) 대비는 거의 없다.
- **"conflict-resolution/extraction을 마음대로 바꿀 수 있음"**이라면: USE는 **실패**(포크 없이는 write path 내부 못 고침). HYBRID(a)도 deterministic freshness를 read 경계에서 *덧씌울* 뿐 엔진 extraction은 못 고침. → BUILD 또는 HYBRID(b)만 통과.
- **"법적으로 깔끔한 exit + 차별화 소유"**라면: USE·HYBRID도 통과(Apache-2.0). 단 이는 *행사할 때만* 지불하는 latent 소유이며, 행사 = 미숙지 코드베이스의 maintainer가 됨.

**깊은 커스터마이즈 vs 환상:**
- **깊은 커스터마이즈 실재**: BUILD(전 축), HYBRID(b)(분기 후), HYBRID(a)(차별화층에 한정).
- **소유의 환상 위험**: USE와 HYBRID(a)에서 "wrapping == owning"이라 주장하는 순간. critique의 핵심 — *forgetting이 엔진 store mutate를 요구하면 (a)는 soft-fork로 전락*하고, 그때 "우리가 통제한다"는 부분 소유 + 분기 이점 0의 최악 조합이 된다. 그리고 멀티테넌트 격리에서 "거짓 격리"(leak은 소유, 원인 코드는 미소유)는 소유 환상의 가장 위험한 형태다.

**정직한 요약:** "우리 것"의 정의를 먼저 못 박지 않으면 이 절의 어떤 비교도 공허하다. 정의가 (1) 차별화 소유면 네 경로 모두 가깝다, (2) write-path까지 마음대로 바꿈이면 BUILD/HYBRID(b)만, (3) 전 라인 in-house(규제·전략)면 BUILD 또는 즉시 풀 포크만 통과.

---

## 5. 의사결정 축 (decision_axes)

각 축이 어느 쪽으로 결정을 기울이는지:

1. **메모리는 제품인가 피처인가** — 제품/플랫폼(vessl가 팔/라이선스)이면 BUILD 또는 HYBRID(b)로. 사이드 피처면 USE/thin-wrap으로 강하게 기움.
2. **'cold'의 정의 (a 비용 / b audit / c KV-cache)** — (a)면 S3+lazy-load 배관에 가까워 "차별화"라기보다 ops → USE/HYBRID 충분. (b)면 Graphiti bi-temporal로. (c)면 MemOS, 세 입장 전부 부적합. *이 축이 사실상 substrate를 결정한다.*
3. **write/read 경로 자체를 소유해야 하는가** — freshness 결정성·write 단위테스트가 hard면 BUILD 또는 HYBRID(b)로. 아니면 USE/thin-wrap이 동일 소유를 싸게 제공.
4. **멀티테넌트 격리/컴플라이언스의 blast radius** — engine-level 강제·증명이 필요하면 BUILD로 기움(또는 어느 경로든 격리를 *gate*로 승격). 단일 cross-tenant leak = breach-class 사건이므로 cold-tiering 차별화보다 상위 gate일 수 있음.
5. **팀 규모·역량·runway·bus-factor** — retrieval+분산시스템 senior 역량 + 상시 eval 커밋 보유 시 BUILD 실현 가능. 소규모/짧은 runway/역량 불확실이면 USE/thin-wrap으로. *이 축은 vessl 실제 팀 데이터가 없어 미측정.*
6. **타임라인 / time-to-value 압박** — 주 단위 필요면 USE/thin-wrap. 분기 단위 허용 + 차별화가 진짜면 BUILD 가능.
7. **규모에서의 LLM extraction TCO** — 고 ingest 볼륨이면 ingest당 토큰 비용이 엔지니어링-월을 압도할 수 있고, write-path 설계(batching, dedup-before-extract, 저렴 모델) 노브는 BUILD/fork에서만 완전 통제 → BUILD로 기움.
8. **업스트림 드리프트/abandonment 무관용도** — 드리프트 무관용 + 포크 유지보수 불가면 BUILD. 외부화 유지보수 선호 + 버전 핀 감수면 USE.

---

## 6. 결정 전에 답해야 할 것 (unknowns_to_resolve)

정답은 아래 답들에 *조건부*다. 답하기 전 전역 verdict는 시기상조다.

1. **'cold'은 정확히 무엇인가?** — 비용 아카이벌(a) / point-in-time audit(b) / KV-cache 재사용(c) 중 무엇인지를 *실제 product owner와* 못 박기. substrate 자체가 여기서 갈린다.
2. **cold-tiering이 진짜 차별화인가, 아니면 warm 데이터의 retrieval 정밀도가 진짜 병목인가?** — 후자면 build-vs-use 프레이밍이 엉뚱한 축을 최적화 중. 사용자가 품질을 느끼는 지점이 어디인지 검증.
3. **"우리 것"의 정의는?** — 차별화 소유 / write-path를 마음대로 변경 / 전 라인 in-house 중 무엇인가 (4절 분해 직결).
4. **forgetting/consolidation이 엔진 내부 store(vector rows, entity links, tombstone) mutate 없이 구현 가능한가?** — Mem0/Graphiti에 clean seam이 실재하는가(plugin/extension API)? 이것이 thin-wrap(a) vs soft-fork(b)를 가르는 *경험적* 질문이며 누구도 확인하지 않았다.
5. **멀티테넌트 격리는 gate인가 feature인가?** — breach blast radius를 감안할 때 cold-tiering보다 상위 요구로 승격해야 하는가?
6. **vessl.ai가 보유한 실제 팀 역량·규모·runway는?** — retrieval / 분산시스템 / bi-temporal / cold-paging 스킬과 상시 eval·(잠재적)포크 유지보수 capacity가 있는가?
7. **예상 ingest 볼륨과 그에 따른 LLM extraction TCO는?** — 토큰 비용이 엔지니어링 비용을 압도하는 구간인가?
8. **embedding-model 결정과 cross-tier 일관성을 어떻게 둘 것인가?** — cold/warm 티어가 vector space를 공유해야 similarity 비교 가능; 재임베딩 마이그레이션 비용은 경로 무관 락인.

---

## 7. 추천 의사결정 절차 (정답 아님, 도달 방법)

전역 verdict를 *지금* 내리지 않는다. critique가 지적한 대로, 엔진 선택은 값싸고 되돌릴 수 있는 *나중* 결정이다. 아래는 경량 프로세스다.

**Phase 0 (≈2–4주, 모든 경로에서 동일한 작업 — 즉 no-regret):**
1. **'cold' 정의 확정** — product owner와 unknown #1, #2, #3을 닫는다. (반나절 워크숍 + 짧은 문서.)
2. **eval harness 먼저 구축** — write/dedup/forget/freshness/cross-tenant leakage를 측정. 이것이 최대·최장수 산출물이고 경로무관이므로 *엔진 선택보다 먼저*가 올바른 첫 수.
3. **두 개의 time-boxed spike 병행:**
   - deterministic freshness PoC (BM25 + versioned extraction + max-over-serial) — 1–2주. BUILD 핵심 thesis의 go/no-go.
   - **Mem0-wrap spike** — clean seam이 실재하는지 *경험적으로* 검증(unknown #4). forgetting이 store mutate 없이 되는가? 안 되면 thin-wrap은 환상.

**Phase 1 (Phase 0 데이터로 채점):**
4. 6번의 unknowns에 답을 채우고, 5번 decision_axes에 대해 우리 상황을 점수화. 특히 #2·#4·#5(팀)·#1(cold 정의)가 대부분의 가중치를 차지할 것.
5. **컴포넌트별로 결정** — 단일 노브가 아니라 6축(storage / write-extraction / conflict-res / read-retrieval / forgetting-tiering / scoping) 각각에 BUILD/USE를 배정. storage·reranker·BM25는 이미 USE 합의. 나머지는 Phase 0 증거로 배정.
6. **격리 gate 통과 확인** — 멀티테넌트 격리가 요구라면, 선택안이 *증명 가능한* 격리를 주는지 별도 게이트로 검증.

**Phase 2:**
7. Phase 0/1로 verdict가 "거의 스스로" 결정되면 진행. 미결이면 thin-wrap으로 PMF·실트래픽·eval 데이터를 생성한 뒤, 데이터가 중요하다고 입증한 부분만 BUILD로 staged migration (reversibility를 1급 전략으로). 이 시간적 옵션("나중에, 컴포넌트별로, 우리 eval 데이터에 근거해 결정")이 가장 강한 수다.

**한 줄 요약:** Phase 0의 세 작업(cold 정의 / eval harness / freshness·seam spike)은 어느 경로든 동일하게 해야 하는 일이다. 그것을 먼저 끝내면 BUILD/USE/HYBRID 질문은 충분한 증거 위에서 — 그리고 십중팔구 컴포넌트별로 — 스스로 답해진다.