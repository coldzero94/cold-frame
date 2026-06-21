# cold-frame UX 설계: 보이는 망각, 되감을 수 있는 믿음

> 목표: cold-frame의 메모리/연결을 **사용자가 보고(see) · 믿고(trust) · 다듬는(curate)** 경험으로 설계한다. 핵심은 남들이 못 하는 "혁신적 메모리 연결/시각화"다. 결론부터: **전역 그래프(global graph)를 히어로로 삼지 않는다.** cold-frame의 무기는 토폴로지가 아니라 **상태(state) — 망각·신선도·믿음의 변화**다.

---

## 1. 리서치 요약

### 1.1 기존 도구가 연결/메모리를 보여주는 방식

세 부류로 갈린다.

- **그래프(graph)형 — Obsidian, mem0(Neo4j), Reflect Map.** force-directed node-link. 노트=점, 링크=선. 첫인상은 강렬하지만 `~200노드`를 넘으면 "**hairball**(털뭉치)"로 무너진다. `~500노드` 이상은 성능도 문제. 레이아웃이 비결정적이라 열 때마다 모양이 달라져 공간 기억(spatial memory)을 못 만든다. 결정적으로 **토폴로지만** 인코딩 — 상태·신선도·중요도·신뢰도가 없다. 같은 노트가 신선·중심이든 낡고·고립이든 똑같이 그려진다.
- **백링크(backlinks)형 — Roam, Logseq, Tana.** 그래프를 부차적으로 두고, 각 노트 하단에 "이 노트를 참조하는 곳" 목록을 띄운다. PKM에서 가장 많이·실제로 쓰이는 연결 UI. Block 단위(원자 사실 단위)라 cold-frame의 atomic fact와 정확히 맞는다. Tana는 graph view를 **아예 안 만들고도** 진지한 knowledge graph 제품을 출시 — query/filtered view가 hairball을 이긴다는 증거.
- **공간 캔버스(spatial canvas)형 — Heptabase.** 카드를 손으로 배치하고 화살표를 그어 **레이블(refutes/causes)** 을 단다. 위치가 안정적이고 의미를 가진다(공간 기억 성립). 단 전부 수작업이라 자동 추출된 수천 사실에는 안 맞고, "내가 안 그은 연결"은 절대 안 드러난다.

### 1.2 진짜로 혁신적이었던 패턴(차용 대상)

- **Contextual backlinks (Roam):** 백링크가 주변 문장 + breadcrumb를 함께 보여줘, 두 사실이 *왜* 연결됐는지(THAT이 아니라 WHY)를 보인다. **모든 PKM 통틀어 가장 가치 높은 연결-표시 패턴.**
- **Typed + directed + labeled edges (Heptabase 화살표 / Excalibrain / Juggl):** 관계가 사람이 읽는 의미를 가진다. 수십 노드 너머에서 그래프를 navigable하게 만드는 **유일한** 요소.
- **Fixed compass layout (Excalibrain):** 부모는 위, 자식은 아래, 형제는 옆 — 결정적·학습 가능한 위치. 랜덤 tangle의 반대.
- **Local/ego-graph only (공통 합의):** 보고 있는 것의 1–2 hop 이웃만. 전 vault가 아니라. 모두가 "실제로 유용"하다고 동의하는 유일한 그래프 모드.
- **Letta 컨텍스트 윈도우 뷰어 + 편집 가능한 메모리 블록 + 토큰 예산 미터:** 모델이 *실제로 보는 바이트*를 라벨링해 보여준다. 발견된 가장 신뢰-구축적 패턴 — "지금 너에 대해 정확히 이걸 안다."
- **Anki/FSRS 망각곡선(forgetting curve):** 메모리 강도(retrievability)를 시간축 차트로. 접근할 때마다 곡선이 re-spike. "쓰면 강해지고, 안 쓰면 흐려진다"를 *볼 수 있게* 만든 유일한 성숙 생태계.
- **Digital garden 성장 단계 글리프(🌱/🌿/🌳) + 'last tended' 날짜:** 차트 없이 글리프 하나로 성숙도/신뢰를 전달. 망각을 "고장"이 아니라 "**돌봄 필요(needs tending)**"로 긍정 프레이밍.
- **mem0의 결단 — 그래프를 *랭킹*에만 쓰고 *표시*하지 않음.** 연결이 hairball로 그려지는 대신 검색 품질에 실제로 기여. "엣지는 그림이 아니라 답을 만들어야 한다."

### 1.3 반드시 피해야 할 함정(PITFALLS)

- **🚨 global hairball trap:** 전역 force-graph는 스크린샷에서만 예쁘고 실사용엔 거의 무용. cold-frame의 자동 추출 사실은 200개를 *빠르게* 넘는다. **전역 그래프를 센터피스로 두는 순간 실패.**
- **그래프를 검색 대체로:** 특정 노드를 시각적으로 못 찾아 결국 이름 검색 → 그래프 존재 이유 소멸.
- **untyped/undirected 엣지:** 모든 링크가 동일 → 왜·어느 방향 연결인지 0 정보.
- **토폴로지만 인코딩:** 상태/신선도/신뢰도 없음.
- **비결정적 레이아웃:** 매번 재배치 → 공간 기억 파괴.
- **맥락 없는 백링크 목록:** 주변 텍스트/breadcrumb 없으면 노드를 정의·설명 못 함.
- **provenance 없는 메모리(ChatGPT):** 출처 없는 사실은 신뢰·보존 불가(~41.5% recall 정확도, "context rot"). 절반(implicit recall)은 목록조차 없어 감사 불가.
- **보이지 않는 silent delete:** ChatGPT는 가득 차면 조용히 삭제. 망각이 cold-frame의 *기능*이지만 **보이고·되돌릴 수 있고·중요표시한 건 절대 안 지워야** 한다.
- **decay를 백엔드 점수로만(mem0):** 사용자가 fade를 *못 보면* 차별점이 안 보인다.
- **clinical 대시보드 느낌:** 차트/막대 과다는 개인 메모리를 분석 콘솔로 만든다.

---

## 2. cold-frame가 남들과 다른 점 (UX 기회)

경쟁 도구가 **구조적으로 못 하는** 시각화는, cold-frame가 스키마에 가진 데이터에서 나온다.

| cold-frame 고유 데이터 (SPEC §2) | 경쟁 도구 상태 | 이게 푸는 시각화 |
|---|---|---|
| **decay_S + access_count + last_accessed** | mem0=백엔드 점수만, Obsidian/Roam=없음, ChatGPT=silent | 노드/카드의 **밝기·크기·망각곡선 sparkline**. 회상 시 re-spike. "기억이 숨 쉰다." |
| **importance** | 거의 없음 | 노드 반경 / 성장 단계. "fading하지만 중요"엔 **pin** 부여. |
| **bi-temporal 4 timestamps (valid_at/invalid_at + created/expired)** | Zep/Graphiti는 데이터만 있고 **UI 0**, 소비자 도구 없음 | **as-of 타임-트래블 스크러버** — "그때 무엇을 *믿었나*". 사실상 아무도 출시 못 함. |
| **supersedes edge + note_history (archive-not-delete)** | ChatGPT=invisible context rot | **믿음이 교정되는 과정(belief-fork)** 을 strikethrough 옛→하이라이트 새 + 원인 소스로. |
| **confidence per fact** | PKM 백링크엔 신뢰 개념 0 | 흐릿함(fuzziness)/discrete state로 불확실성을 pre-attentive하게. |
| **provenance (sources: kind/ref/role/observed_at)** | ChatGPT/mem0/Saner 전부 숨김 | "이 기억이 왜 존재하나" — 원본 메시지/문서로 클릭 이동하는 **provenance 트레일**. |
| **typed edges (supersedes/derived_from/caused_by/relates_to/mentions)** | Obsidian=undifferentiated gray | 각 관계를 별개 시각 채널 + **필터 칩**. |
| **consolidation worker (derived_from 요약, soft-archive)** | 전부 없거나 silent | "**밤새 내 메모리가 한 일**" 감사 가능·되돌릴 수 있는 ledger. |
| **REINFORCE side-effect (읽기 시 access_count++/decay_S++, SPEC §5)** | — | 사실이 surface되는 *그 행위*가 강화 신호 → UI와 엔진 강화가 **같은 이벤트**. |
| **promiscuity down-weight (1/(1+0.001(n-1)²), SPEC §5)** | — | 과연결 junk hub를 "수상하게 밝고 바쁜 노드"로 flag → 분할/정리. |

핵심 통찰: **경쟁자들은 "사실들이 어떻게 연결되나"(정적 토폴로지)를 보여준다. cold-frame만이 "내 믿음이 *시간에 따라 어떻게 살아 움직이는가*"(망각·강화·교정)를 보여줄 수 있다.** 이게 히어로가 되어야 한다.

---

## 3. 컨셉 비교

4개 컨셉 모두 리서치 합의(local-only graph, list/card-first, decay 가시화, archive-not-delete, provenance-first)를 잘 지킨다. 차이는 **무엇을 히어로 축으로 삼는가**다.

| 컨셉 | 히어로 축 | 강점 | 약점 / 리스크 | 혁신성 | 실현성 | 이기는 순간 |
|---|---|---|---|---|---|---|
| **Constellation** (별자리) | 공간(살아있는 ego-graph) | 상태를 밝기/크기/흐림에 인코딩한 *살아있는* 그래프. fixed compass = 결정적. as-of로 별자리 재형성. | 캔버스는 일상 90% 작업에서 list/search에 밀린다(Tana 교훈). promiscuous hub에서 node cap이 정작 봐야 할 걸 가린다. "장식 그래프"로 회귀할 유혹. | 높음 (살아있는 그래프 + 되감기) | 높음 | 회상 직후 sanity-check, junk hub 발견, 놓친 연결 ghost edge 제안 |
| **Rewind** (타임라인) | 시간(bi-temporal lifeline) | 시간을 1급 축으로. lifeline 두께=강도, belief-fork가 보인다. Replay 타임랩스 = 강한 onboarding wow. | **lane/topic 레이아웃이 hairball을 수평으로 옮길** 위험(수백 lifeline). cold-start가 빈약(몇 주 데이터 필요). 시간이 기본 축이면 "지금 X 다 보여줘"엔 과함. valid vs transaction time 혼동. | 매우 높음 (as-of를 *홈*으로) | 높음 | "그때 무엇을 믿었나" 회복, 이해의 진화 Replay, belief-fork 감사 |
| **Margin** (여백/HUD) | 맥락(작업 현장 in-context) | 메모리가 작업하는 곳에 뜸. 회상된 것 + WHY(provenance) + 1키 교정. see→trust→correct를 in-context로 닫는 유일 설계. | 가장 풍부한 in-context 렌더(오버레이/statusline)는 Claude Code/터미널 surface가 허용해야 가능 → 보장 fallback은 텍스트 카드로 덜 ambient. 토스트 남발 = 잔소리. ambient 레이어가 가장 늦게 슬립될 위험(P3/P4). | 매우 높음 (in-context closed loop) | 인스펙터=높음, ambient=중간 | 세션 중 stale recall을 그 자리에서 교정, "왜 기억해?" 1키 |
| **Greenhouse** (온실) | 건강(돌보는 정원) | 망각 자체를 인터랙티브 surface로. triage queue=0 만들기. **사실 수가 줄어드는 게 보상**(anti-hoarding). garden glyph로 따뜻함. | P4(decay+consolidation) 엔진이 실제·잘 튜닝돼야 함. 결정을 너무 많이 떠넘기면 chore. clinical 위험. cold-start 빈약. 추출 문장이 못생기면 정원이 초라. 큐레이션을 *원하는* 사용자 가정. | 매우 높음 (forgetting을 제품화) | 높음 (P4의 human skin) | 아침 2분 점검, 주말 큐레이션, 잘못된 auto-archive Revive |

### 솔직한 비평: 혁신적이지만 실용 vs 멋지지만 무용

- **공통 진짜 무기 = "as-of 타임-트래블"와 "belief-fork(supersession story)".** 네 컨셉 모두 이걸 핵심으로 든다. 둘 다 cold-frame만 *스키마상* 가능하고, 어떤 소비자 도구도 출시 못 했다. **이게 진짜 혁신.** 멋지면서 실용적(과거 맥락 회복 + 교정 감사라는 실제 job).
- **Constellation의 공간 캔버스 = 가장 "멋지지만 위험".** 살아있는 그래프는 시각적으로 강렬하나, 리서치가 반복 경고한 대로 일상 작업의 90%는 list/search다. 이걸 *프라이머리*로 두면 장식 함정으로 회귀하기 쉽다. → **세컨더리 진단 렌즈로 강등**이 옳다.
- **Rewind의 "시간이 기본 축" = 가장 위험한 디폴트.** "지금 커피에 대해 아는 것 다 보여줘"는 평평한 필터 리스트가 타임라인보다 빠르다. 그리고 cold-start가 비어 보인다. → 타임라인을 *전용 모드*로 갖되 홈으로 강제하지 않는다.
- **Margin의 in-context HUD = 가장 차별적이지만 surface 의존.** 진짜 가치(작업 현장 see→trust→correct)는 크나, ambient 렌더는 Claude Code surface 제약에 묶인다. → **MCP recall-receipt(텍스트 카드)로 확실히 출시 가능한 부분 먼저**, 풍부한 오버레이는 점진.
- **Greenhouse의 정원 메타포 = 가장 따뜻하고 "돌봄" 프레이밍에 최적.** 망각을 긍정·되돌릴 수 있는 일상 의식으로 만든다. 단 P4 엔진이 약하면 글리프가 자의적으로 느껴진다.

**결론: 어느 하나를 통째로 고르지 않는다.** 네 컨셉은 사실 *같은 데이터의 네 진입점*이다 — 건강(Greenhouse) · 시간(Rewind) · 맥락(Margin) · 공간(Constellation). 최선은 **하나의 일관된 제품 안에서 이들을 모드/레이어로 합치되, 히어로를 명확히 하나로 정하는 것**이다.

---

## 4. 추천 방향 (hero)

### 4.1 한 줄 방향

> **프라이머리 surface = list/card 기반 "지금 내가 너에 대해 아는 것" 인스펙터(Greenhouse 홈) + 항상 떠 있는 as-of 타임-트래블 스크러버 + 작업 현장 in-context recall-receipt(Margin).** 히어로 시각화 = **"믿음이 교정되는 과정을 되감아 보는" Belief-Fork × As-of Time-Travel** — 망각·강화·supersession을 *살아있고 되돌릴 수 있는* 스토리로 만든 것. **그래프는 포커스 사실의 로컬 1–2 hop ego 렌즈로만**, 절대 전역 hairball로 두지 않는다.

이유: 리서치 합의(list-first, local-only graph, provenance-first, decay-visible-positive)를 정면으로 따르면서, cold-frame만 가능한 두 무기(보이는 망각 + 되감을 수 있는 믿음 교정)를 히어로로 올린다. Greenhouse의 따뜻한 건강-홈 + Margin의 in-context 루프 + 공통 as-of/belief-fork 히어로를 합성한다. Constellation의 별자리는 P4 이후 옵션 렌즈로 흡수, Rewind의 Replay는 onboarding wow 모드로 흡수.

### 4.2 히어로 컨셉: **"Belief-Fork Time-Travel" — 되감을 수 있는 믿음**

cold-frame의 두 깃발(망각 + 결정적 충돌해결)은 둘 다 *보이지 않는다*. 히어로는 이 둘을 하나의 watchable·reversible 인터랙션으로 융합한다.

1. **As-of 스크러버**가 항상 chrome에 핀. 끌면 전체 뷰가 그 시점 믿음-상태로 재구성(`valid_at<=as_of<invalid_at`, 엣지도 동일 술어). 두 시간축을 명시 토글로 분리: **"내가 믿었던 것(transaction)" vs "사실이었던 것(valid)"**.
2. **Belief-fork**: 교정된 사실은 strikethrough 옛 → supersedes 화살표(결정적 freshness 이유 + 원인 소스) → 하이라이트 새. 스크러버를 과거로 끌면 *교정이 un-correct되어* 옛 믿음이 다시 active로 살아난다. 놓으면 snap-forward하며 다시 교정된다.
3. **Pin/Let-go의 즉각 결과**: fading 사실을 Pin하면 망각곡선이 re-spike하고 카드가 밝아지며 성장 단계가 오른다. Let-go하면 흐려지며 'recently faded' shelf로 미끄러진다(status=archived, 항상 Revive 가능). **망각이 보이고·합의되고·되돌릴 수 있는 행위.**

이건 데모-friendly 함정(전역 그래프)을 거부하고, 데이터가 *진짜로* 가능케 하는 단 하나를 한다: **자기 마음을 되감아 믿음이 교정되는 걸 보는 것.** 어떤 PKM/AI-메모리 도구도 살아있는·decaying 그래프 위에서 타임-트래블을 출시 못 했다.

### 4.3 핵심 화면

**화면 A — Home: "지금 내가 너에 대해 아는 것" (list/card-first, 히어로 surface)**

```
+---------------------------------------------------------------------+
|  cold-frame · coby의 메모리        [ search facts...      ] [filter v]|
|  overnight: +6 강화  3 병합  2 fade  | * 결정 대기 2건               |
|  active facts: 418 (-3 이번주, 더 lean)   ◀━━●━ as-of: TODAY ━▶      |
+---------------------------------------------------------------------+
|  > 네가 필요한 결정 (2)               [전부 해결 ->]                  |
|    [!] 충돌: "works at Vessl" vs "works at Anthropic"                |
|    [~] 저신뢰(0.41): "prefers tabs over spaces"  확인?               |
+---------------------------------------------------------------------+
|  EVERGREEN (강하고 자주 씀)            sort: strength v               |
|   🌳 "coby prefers dark roast"   S██████  conf▓▓▓  src3  ↔2  used9   |
|   🌳 "main editor = neovim"      S█████   conf▓▓▓  src5  curve╱╲╱╲   |
|  BUDDING                                                             |
|   🌿 "timezone is KST"           S███     conf▓▓   src1              |
|  FADING (돌보거나 보내기)                                            |
|   🌱 "liked teal theme"          S█·      last 71d   [pin][let go]   |
|   ·  "used pnpm (old proj)"      S·       ~2일 후 archive            |
+---------------------------------------------------------------------+
|   consolidation 매일 밤 실행 · [지금 돌보기] · [⟲ 타임-트래블]       |
```

- 글리프(🌱/🌿/🌳)는 confidence×decay×access에서 *파생*(digital garden 패턴), hover로 raw 입력(last_accessed/access_count/confidence/source) 노출 → black box 아님.
- search/filter/sort는 v1부터 필수(ChatGPT의 unscrollable list 교훈): type/edge type/confidence threshold/decay strength/source.

**화면 B — Fact Detail: 강도 · provenance · belief history (트러스트/큐레이션 패널)**

```
+-- FACT --------------------------------------------------------------+
| "coby works at Anthropic"          conf ▓▓▓░   🌳   access×7         |
| 망각곡선:  1│ *    *      *    *        (각 * = 사용 시점, re-spike)  |
|           0│  `._/ `.__./ `._./  ───────────────────────> time       |
| score = .42 recency + .30 importance + .18 relevance  [입력 보기]    |
| ── belief history (교정 스토리) ──────────────────────────────────  |
|   ~~"works at Vessl"~~  superseded 2026-06  (msg#a17)               |
|        │ supersedes  (이유: valid_at 더 최신 ⇒ 자동)                |
|   "works at Anthropic"  ← 현재 active                                |
| ── provenance (왜 존재하나) ──────────────────────────────────────  |
|   • chat 2026-06-12 14:02  role:user  "I joined Anthropic"  [열기]  |
|   • doc  offer-letter.pdf  2026-06-12                       [열기]  |
| ── nearby (1–2 hop, 유일한 그래프) ──── filter ▸ type ▸ conf ▸ ──── |
|   supersedes→ "works at Vessl" [archived]                           |
|   relates_to→ "based in SF"                                         |
| [pin] [edit(versioned)] [reinforce] [revert v1] [let go] [split]    |
+----------------------------------------------------------------------+
```

**화면 C — Belief-Fork × Time-Travel (히어로 인터랙션)**

```
+--------------------------------------------------------------------+
| ⟲ TIME-TRAVEL   [ 내가 BELIEVED ● | 사실이 TRUE ○ ]                |
| 2024 ─────────[●]─────────────────────────────── 2026 (today)      |
|               ^ as_of = 2025-04-12                                 |
+--------------------------------------------------------------------+
|  belief-fork @ 끌어서 통과 중:                                     |
|   was ▸ "works at Vessl"  ̶(̶2̶0̶2̶5̶-̶0̶9̶→̶2̶0̶2̶6̶-̶0̶6̶ 믿음)̶                  |
|          │ supersedes  (valid_at 더 최신 ⇒ 자동)                   |
|          ▼  caused_by: "I joined Anthropic"  ↗msg#a17             |
|   now ▸ "works at Anthropic"   conf ▓▓▓░                          |
|                                                                    |
|  as-of 2025-04-12 기준 믿음:                                       |
|   🌳 "works at Vessl"  (그땐 현재 · 2026 supersede됨, 지금 되살아남)|
|   (ghost) "works at Anthropic" — 2026-06까지 미지                  |
|                                            [오늘로 복귀]           |
+--------------------------------------------------------------------+
```

**화면 D — In-context Recall Receipt (Margin, 작업 현장)**

```
┌─ cold-frame · 이 턴에 4개 사실 회상 ───────────────────────┐
│ ● deploys via edge workers (Cloudflare)   c:0.91          │
│   from msg#3f2 · "moved auth to edge" · 5d ago            │
│ ◐ prefers pnpm over npm                   c:0.78  used6×  │
│ ○ works at Anthropic (since 2026-06)      c:0.95          │
│   supersedes "works at Vessl"                            │
│   ──────────────────────────────────────────────────    │
│   [a]ccept all  [x]correct  [w]hy?(→Fact Detail)  [/]mute │
└───────────────────────────────────────────────────────────┘
  ● strong  ◐ fading  ○ at-risk
```

### 4.4 핵심 인터랙션

- **Tend loop**: fading 사실에서 Pin/Let-go 한 번 → 결과가 즉시·legibly 애니메이트(곡선 re-spike & 밝아짐 / 흐려지며 shelf로). 위치 재배치 없음(opacity/size만 변경 — jitter 함정 회피).
- **Correct-in-place**: 회상 카드에서 stale 사실에 1키 → 엔진이 bi-temporal supersession 기록(옛 archive + invalid_at + supersedes edge + 원인 메시지). 같은 세션 다음 회상은 이미 교정됨.
- **As-of 드래그**: 프레임당 파라미터화된 as_of 쿼리(녹화가 아닌 재구성). 전이는 opacity/size만, 결코 reposition 안 함.
- **Triage to zero**: 엔진이 결정적으로 못 푸는 것만(진짜 모순/모호 병합/저신뢰/pin 인접 archive 후보) 큐에. 큰·되돌릴 수 있는 동사(Keep new/Keep old/Both true/Merge/Pin/Let go/Snooze).

### 4.5 MVP → 풍부화 로드맵 (SPEC 빌드 단계에 매핑)

| 단계 | UI 산출물 | 엔진 의존(SPEC) | 비고 |
|---|---|---|---|
| **P1 (CLI baseline)** | `show <id>`에 provenance+버전+edge 텍스트 렌더, `timeline`, `stats` (rich 테이블). MCP `search_memory`가 **recall-receipt(텍스트 카드)** 반환 | 이미 있음 (sources/edges/note_history/SearchResult) | Margin의 보장-출시 fallback이 여기서 무료로 나옴 |
| **P3 (읽기품질+화면)** | **로컬 웹 UI `cold-frame ui`** — 화면 A(Home list/card) + 화면 B(Fact Detail: provenance 트레일 + belief history + 로컬 ego-edge) + **as-of 스크러버 v1**. read-mostly | bi-temporal 컬럼·sources·note_history·edges는 전부 존재. as-of = 기존 `search(as_of=T)` 재렌더 | provenance/믿음-히스토리는 P2 데이터면 충분 → P3에서 바로 시각화 가능 |
| **P4 (망각)** | 화면 A의 FADING band + 망각곡선 sparkline + Pin/Let-go **Tend loop** + "밤새 메모리가 한 일" consolidation ledger + Triage queue. **decay 가시화가 진짜 히어로** | decay_S/importance/access_count + consolidation worker + capacity cap (P4 산출물) | Greenhouse는 본질적으로 **P4 엔진의 human skin** |
| **P5+ (옵션 렌즈)** | Constellation **ego-별자리**(살아있는 로컬 그래프, fixed compass) · Rewind **Replay** 타임랩스(onboarding wow) | 망각곡선 full history엔 작은 per-access 로그 추가 필요(아래 §5) | 별자리는 *세컨더리 진단*으로만, 장식 회귀 금지 |

**CLI가 웹 UI를 보완하는 방식:** CLI는 *캡처·빠른 조회·스크립트*(add/search/list/show/timeline/path), 웹 UI는 *큐레이션·트러스트·타임-트래블*. MCP recall-receipt은 *작업 현장 표면*(Margin). 셋은 같은 코어/같은 `.db`를 본다. 일상 회상의 90%는 CLI/search/MCP로, 웹 UI는 "보고 믿고 다듬는" 의식적 순간을 위한 곳.

---

## 5. 엔진 정합 & 다음 단계

### 5.1 UI ↔ SPEC 매핑 (대부분 이미 존재)

| UI 요소 | SPEC 근거 | 추가 필요? |
|---|---|---|
| Home 카드(content/conf/source/edge count) | `Note`(§2), `sources`, `edges` | 없음 |
| 성장 글리프 + 강도 미터 | `decay_S`/`importance`/`access_count`/`last_accessed`(§2 R5) | 없음(파생 계산만) |
| 망각곡선 sparkline | `e^(−Δt/decay_S)`(§6) + last_accessed | **per-access 로그 필요**(아래) |
| belief history / 교정 스토리 | `supersedes` edge + `note_history`(update_type)(§2,§4) | 없음 |
| provenance 트레일 | `sources`(kind/ref/role/observed_at)(§2) | 없음 |
| 로컬 ego-graph(1–2 hop, typed) | `edges`(relation/weight) + `neighbors()`(§3) | 없음(인덱스된 2-hop) |
| as-of 타임-트래블 | bi-temporal 4 타임스탬프 + `search(as_of=)`(§5 step1) | 없음(엣지에도 동일 술어 적용) |
| Pin / Let-go / Revive | `status`(active/archived) + `importance`/`decay_S` 변이 + `touch()` | 없음(기존 변이 재사용) |
| Correct-in-place | WriteCore conflict 경로(§4) + note_history revert | 없음 |
| consolidation ledger | `jobs` 큐 + `derived_from` edges + status 변경(§6) | 없음(read) |
| recall-receipt(Margin) | `SearchResult{hits:[{note,score,signals}]}`(§5) | MCP 출력 포맷팅만 |
| Triage queue | 엔진이 auto-resolve 못 한 모순/모호(§4 "모호분만 배치 LLM") | 엔진이 "held for human" 플래그 노출 권장(아래) |

### 5.2 엔진이 UI를 위해 노출/추가해야 할 것 (최소)

1. **per-access 이벤트 로그(작지만 유일한 진짜 추가):** 스키마는 현재 running `access_count` + `last_accessed` 스칼라만 → 망각곡선은 *최근 re-spike*만 그릴 수 있고 *전체 history*는 못 그린다. 작은 `access_log(note_id, ts)` 테이블 또는 주기 스냅샷 추가. 이게 망각곡선·정직한 과거-강도 Replay를 가능케 하는 단 하나의 schema 변경. (degrade gracefully: 없으면 현재 강도만)
2. **"human-review 보류" 플래그/뷰:** WriteCore가 결정적으로 못 푼 모순/모호 병합/저신뢰를 Triage queue가 읽을 수 있게 노출(예: `jobs` 상태 또는 쿼리). 엔진이 *대부분*을 auto-resolve하고 *소수*만 surface해야 Tend가 chore 안 됨(Greenhouse의 핵심 리스크).
3. **decay 점수 입력의 투명 노출:** Fact Detail의 "입력 보기"가 `recency/importance/relevance` 분해를 보여줄 수 있게 read API. 글리프가 black box로 느껴지지 않게 하는 신뢰 장치.
4. **read-mostly UI 엔드포인트(P3 `[ui]` extra):** (a) active 사실 list(scope/status/decay-sort), (b) fact detail join(sources+note_history+1-hop edges), (c) as-of snapshot(기존 temporal filter), (d) supersedes 기반 fork list, (e) consolidation ledger(jobs+derived_from). 전부 단일파일 인덱스 SELECT — 개인 규모에서 빠름.

### 5.3 구체적 다음 설계 단계

1. **as-of 스크러버의 두 시간축 라벨링 UX 확정** — "내가 BELIEVED" vs "사실이 TRUE" 토글의 카피·기본값·시각 차별을 정밀 설계(bi-temporal 혼동이 리서치가 경고한 핵심 함정).
2. **성장 글리프 매핑 함수 정의** — confidence×decay×access → 🌱/🌿/🌳 경계값과 hover 입력 노출. 자의적으로 느껴지지 않게 엔진 점수와 1:1 검증.
3. **Triage queue 진입 기준 설계** — 엔진이 무엇을 auto-resolve하고 무엇을 보류할지의 경계. "큐가 너무 길면 chore" 리스크를 막는 게이트.
4. **불확실성 렌더링 규칙** — confidence를 fuzziness/discrete state(strong/fading/at-risk)로(ambient), raw decimal은 Fact Detail에서만(false precision 함정 회피).
5. **MCP recall-receipt 포맷 스펙** — `SearchResult` → 텍스트 카드(provenance shorthand 포함) 출력. P1에서 출시 가능한 Margin의 확실한 부분.
6. **전이 애니메이션 제약 명문화** — opacity/size만 변경, reposition 금지(공간 기억 보존, jitter 함정 회피). 모든 뷰 공통 규칙으로.
7. **cold-start 대응** — 빈 DB에서 타임라인/Replay/FADING이 비어 보이는 문제 → 신규 사용자엔 Home list + provenance 중심으로, 시간/망각 뷰는 데이터가 쌓이면 점진 강조하는 progressive disclosure.

### 5.4 의도적으로 *안 하는* 것

- 전역 force-directed 그래프를 히어로/프라이머리로 두지 않는다(hairball 함정).
- Heptabase식 수작업 캔버스를 프라이머리로 두지 않는다(자동 추출 수천 사실에 안 맞음 — 옵션 큐레이션 surface로만).
- 모든 write에 토스트를 띄우지 않는다(믿음이 *변할 때*만 — supersession/conflict).
- decay를 백엔드 점수로만 두지 않는다(반드시 보이게 — 이게 차별점의 전부).
- 어떤 것도 silent delete 하지 않는다(archive-not-delete, 항상 Revive).
---

# Part 2: 구체 프로그램 UX

> Part 1이 "왜 이 히어로인가"를 정했다면, Part 2는 **사용자가 실제로 보고 조작하는 프로그램**을 확정한다. 한 줄 요약: cold-frame는 **하나의 `.db`를 세 시점에서 만지는 하나의 제품**이다 — CLI로 빠르게 캡처/조회하고, Claude Code(MCP)에서 일하며 회상-영수증으로 교정하고, 로컬 웹 UI에서 의식적으로 **보고(see)·믿고(trust)·다듬는다(tend)**. 아래는 그 제품의 정보 구조, 핵심 플로우, 비주얼/인터랙션 언어, 상태/엣지 케이스, 그리고 남은 결정이다.

---

## 6. 정보 구조(IA) & 내비게이션

### 6.1 앱 지도 — 한 창, 한 `.db`, 한 사용자

웹 UI는 단일 창·단일 사용자 "정원 돌보기" 앱이다. 항상 떠 있는 **상단 chrome**과 일상 사용 빈도순으로 정렬된 **좌측 6개 섹션**으로 구성된다.

```
cold-frame ui  (한 창 · 한 .db · 한 사용자)
│
├─ 영구 CHROME (항상 보임, 절대 자리 안 바뀜)
│   ├─ 브랜드 + db 헬스 ···  "cold-frame · coby"  (green/amber dot = doctor 상태)
│   ├─ 전역 검색 ··········  / 누르면 포커스. 결과는 현재 as_of + scope 범위로 한정
│   ├─ AS-OF 스크러버 ······  핀된 히어로 컨트롤 (섹션이 아니라 전역 MODE)
│   │      ◀━━●━━▶  as-of: TODAY   [ 내가 BELIEVED ● | 사실이 TRUE ○ ]
│   ├─ scope / user 스위치 ·  user ▸ agent ▸ session  (단일 사용자, 멀티 스코프)
│   └─ 테마 ·············  ☀ / 🌙  (따뜻한 "garden" 기본)
│
└─ 좌측 RAIL (6 섹션 — 순서 = 일상 사용 빈도)
    1. 🏡 Home        인스펙터: "지금 내가 너에 대해 아는 것"
    2. 🔎 Fact        Fact Detail (카드 클릭으로 진입, deep-link 가능)
    3. ⟲ Time-Travel  belief-fork × as-of 풀블리드 surface
    4. 🌱 Tend        Triage 큐 + Faded shelf (엔진이 자동 해결 못한 것만)
    5. 📜 Ledger      "consolidation이 한 일" — 감사·되돌리기 가능
    6. ⚙ Settings     scope 기본값/embedder/LLM/decay 튜닝/export·import/doctor
```

**Rail 배지는 차분하다.** Tend만 triage 카운트(유일하게 nag 가치 있는 배지), Ledger는 "n overnight"를 본 적 없을 때까지 점으로 표시. 배지는 차분한 dot이며, **진짜 미해결 충돌일 때만** 빨강이 된다.

### 6.2 라우팅 / URL — 평평하고 공유 가능, 믿음-상태가 인코딩됨

```
/                    → Home (today, BELIEVED)
/fact/:id            → Fact Detail
/time-travel         → Time-Travel surface
/tend                → Triage 큐        /tend/faded → Faded shelf
/ledger              → consolidation ledger   /ledger/:jobId → 한 작업의 diff
/settings/:pane      → settings 패널
/search?q=...        → 검색 결과 (오버레이로 인라인 렌더도)

전역 QUERYSTRING (어떤 route에도 적용, chrome이 설정):
  ?as_of=2025-04-12     타임-트래블 지점 (없으면 today)
  ?lens=believed|true   bi-temporal 토글 (기본 believed)
  ?scope=user:coby      scope 스위치 (기본 = config 기본값)
```

**핵심 규칙: 스크러버와 토글은 route가 아니라 querystring을 바꾼다.** Home에서 스크러버를 끌면 `/?as_of=...`에 머물고 Home 전체가 그 믿음-상태로 재구성된다. 모든 믿음-상태가 **복사-붙여넣기 가능한 permalink**가 되고, Time-Travel은 같은 파라미터의 더 풍부한 surface일 뿐이다. "오늘로 복귀" = `as_of` 비우기.

### 6.3 섹션 간 이동 — 공간 기억(spatial-memory) 계약

- **좌측 rail + chrome은 고정 가구다** — 절대 자리를 안 바꾼다. "Tend는 4번째, 스크러버는 상단 중앙"이라는 공간 기억이 앱 전체에서 유지된다(카드를 지배하는 opacity/size-only 규칙이 내비게이션도 지배).
- **Home → Fact Detail**: 카드 클릭 → Detail이 **Home 위로 우측 drawer**로 슬라이드. 인스펙터 리스트는 뒤에 그대로 있고, URL만 `/fact/:id`로 갱신(deep-link용). drawer를 닫으면 정확히 그 스크롤 위치로 복귀.
- **Fact Detail → Time-Travel**: belief-history 블록의 "⟲ rewind this correction" 링크 → 그 사실의 supersession과 fork 직전 `as_of`로 pre-seed된 Time-Travel을 연다.
- **어디서든 → Tend**: Home의 "결정 대기 (n)" 배너와 rail 배지 둘 다 `/tend`로 deep-link. 항목 해결은 opacity로 사라지며 배지 감소 — Home을 절대 끌어당기지 않는다.
- **Ledger → Fact / Tend**: 각 ledger 행(consolidation/merge/auto-archive)이 영향받은 사실로 링크되고 인라인 **Undo**(재활성) 제공 — archive-not-delete 보장 동일.
- **as-of 스크러버는 섹션 전환에도 유지된다**: Home에서 2025로 타임-트래블한 뒤 Tend로 가면 여전히 2025. "과거 믿음 보는 중 — 오늘로 복귀" 어포던스가 끈질기게 따라와 사용자가 시간 속에서 길을 잃지 않는다.

### 6.4 내비게이션 와이어프레임 (Home + chrome + drawer)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ● cold-frame · coby      [ / search facts… ]   scope: user:coby ▾    ☀ 🌙   │
│ ◀━━━━━━━●━━━━━━━━━━━━━▶  as-of: TODAY        [ 내가 BELIEVED ● | 사실 TRUE ○ ]│
├────────┬─────────────────────────────────────────────────────────────────┤
│ 🏡 Home │  ▸ 네가 필요한 결정 (2)                          [전부 해결 →]   │
│ 🔎 Fact │    [!] 충돌: "works at Vessl" vs "works at Anthropic"           │
│ ⟲ Time │    [~] 저신뢰(0.41): "prefers tabs over spaces"   확인?          │
│ 🌱 Tend●2│  ─────────────────────────────────────────────────────────────  │
│ 📜 Ledg•│  EVERGREEN                                   sort: strength ▾    │
│ ⚙ Set  │   🌳 coby prefers dark roast   S██████ conf▓▓▓ src3 ↔2 used9     │
│        │   🌳 main editor = neovim      S█████  conf▓▓▓ src5 curve╱╲╱╲    │
│        │  BUDDING                                                          │
│        │   🌿 timezone is KST           S███    conf▓▓  src1               │
│        │  FADING (돌보거나 보내기)                                          │
│        │   🌱 liked teal theme          S█·  last 71d   [pin] [let go]     │
│        │  ───────────────────────────────────────────────────────────────│
│        │  overnight: +6 강화 · 3 병합 · 2 fade   [ledger 보기 →]          │
└────────┴─────────────────────────────────────────────────────────────────┘
        (카드 클릭 → Fact Detail drawer가 우측에서 슬라이드 인,
         Home 리스트는 뒤에 그대로; URL → /fact/:id)
```

### 6.5 세 surface가 하나의 제품이 되는 방식

```
   캡처 / 빠른 조회           작업 현장                    보고·믿고·다듬기
   ┌────────────┐            ┌─────────────┐               ┌──────────────┐
   │  CLI        │           │  MCP 영수증  │               │  웹 UI        │
   │ add/search  │           │ Claude Code: │               │ cold-frame ui  │
   │ recall/list │           │ 사실+WHY+1키  │               │ Home/Fork/    │
   │ stats/path  │           │              │               │ Tend/Ledger   │
   └─────┬───────┘           └──────┬──────┘                └──────┬───────┘
         └────────────── 한 코어 / 한 ~/.cold-frame/memory.db ──────┘
```

**한 제품처럼 느끼게 하는 규칙:**

- **어디서나 같은 어휘**: 글리프 🌱🌿🌳, 상태 dot ●strong ◐fading ○at-risk, "supersedes / belief-fork", "Tend", "Faded shelf", "let go / revive"가 CLI rich 테이블·MCP 영수증 카드·웹 UI에서 **바이트 단위로 동일**하게 나온다.
- **Deep link가 surface를 잇는다**: 모든 사실은 안정적 id를 갖는다. MCP 영수증의 `[w]hy?`와 CLI `show <id>` 푸터가 둘 다 `ui: http://localhost:27182/fact/<id>`를 출력 → 터미널→웹 원클릭 점프. 웹 Fact Detail은 `cli: cold-frame show <id>`를 보여줘 되돌아갈 수 있게 한다.
- **회상 provenance는 cross-surface**: MCP `[x]correct`나 CLI `tend`로 한 교정이 웹 Ledger·Tend 히스토리에 "from recall · msg#a17" breadcrumb로 나타난다 — 세 도구가 한 메모리를 공유함을 사용자가 본다.
- **역할 분담을 명시(흐리지 않음)**: 빈 DB Home과 Settings 둘 다 한 줄을 단다 — "CLI가 캡처, Claude Code(MCP)가 일하며 회상, 이 UI는 다듬는 곳." **웹 UI는 캡처 도구가 아니다**(큰 "add fact" CTA로 CLI/MCP와 경쟁하지 않음). CTA는 tend/trust/time-travel 동사뿐.
- **단일 실행**: `cold-frame ui`가 브라우저를 `/`로 연다. 스크립트용은 `cold-frame ui --no-open`. 포트는 고정/설정 가능(예: **27182**)이며 doctor에 노출 — 그래야 CLI/MCP가 출력하는 ui URL이 stale되지 않는다.

### 6.6 Cold-start / 점진 노출 (IA 레벨)

거의 빈 DB에서도 rail은 6 섹션을 모두 보여준다. 단 **Time-Travel과 Ledger는 빈 화면 대신 설명 empty-state를 렌더**한다: "Time-Travel은 믿음이 교정되기 시작하면 켜집니다 — cold-frame를 계속 쓰면 당신의 과거가 여기서 되감을 수 있게 됩니다." Home은 provenance-rich 카드를 앞세우고, FADING/Tend band는 decay 데이터가 생길 때까지 접혀 있다. 섹션은 절대 나타나거나 사라지지 않는다(공간 기억 보존). 이로써 "빈 타임라인이 고장처럼 보이는" 함정을 피하면서 IA를 안정적으로 유지한다.

---

## 7. 핵심 사용자 플로우

### 7.1 플로우 1 — 첫 실행 / 온보딩: 무서운 빈 그래프 없는 cold-start

**7.1.1 설치 + `cold-frame setup` (4 비트)** — 한 번 붙여넣기. 대화형·idempotent하며 항상 구체적 "다음에 할 일"로 끝나 빈 UI에 떨구지 않는다.

```
$ uv tool install cold-frame && cold-frame setup

  cold-frame · setup
  ─────────────────────────────────────────────
  ✔ 1/4  memory file   ~/.cold-frame/memory.db  (생성됨, 0 facts)
  ✔ 2/4  embedder      offline HashEmbedder (키 불필요) ·
                       더 나은 recall? 나중에 `cold-frame config embedder`
  ✔ 3/4  Claude Code   MCP "cold-frame" 등록됨  (scope: user)
                       → Claude Code가 이 메모리를 읽고 쓸 수 있음
  ✔ 4/4  doctor        all green

  준비 끝. cold-frame는 일부러 비어서 시작합니다 — 쓰면서 당신을 배웁니다.

  이 중 하나를 해보세요:
    ▸ cold-frame add "I prefer dark roast and use neovim"
    ▸ 그냥 Claude Code를 계속 쓰기 — 일하면서 사실이 캡처됩니다
    ▸ cold-frame ui        정원 열기 (지금은 비어 있음 — 괜찮습니다)
```

규칙: 모든 스텝이 *해결된 값*(경로/embedder/scope)을 출력해 숨기는 게 없다. `claude` CLI가 없으면 스텝 3은 "wrote .mcp.json — Claude Code 재시작"으로 degrade(절대 에러 아님). doctor의 red 라인은 정확한 fix 명령으로 링크.

**7.1.2 첫 `add` — 보이지 않는 엔진을 즉시 legible하게.** 첫 write가 "작업을 보여줘야" 사용자가 모델이 *원자 사실 + provenance*임을 배운다(notepad가 아님).

```
$ cold-frame add "I prefer dark roast and I just moved to neovim from vscode"

  1개 노트에서 2개 사실 캡처:
   🌱 "coby prefers dark roast"           semantic · conf ▓▓▓ · src: you, 방금
   🌱 "coby's main editor is neovim"      semantic · conf ▓▓░ · src: you, 방금
        ↳ 전환 감지: was vscode? (아직 파일에 없음 — supersede할 게 없음)

  사실 2개가 자라는 중. 보기: cold-frame list
```

트러스트 모먼트: 복합 문장을 split(원자성 학습), provenance 태깅("src: you, 방금"), 그리고 supersede할 게 없어도 *무엇을 찾았는지 명명* — 나중 진짜 교정이 일관되게(마법이 아니라) 느껴지게 한다.

**7.1.3 빈 Home — 빈 그래프가 아니라 garden bed.** 거의 빈 DB의 `cold-frame ui`: 시간 스크러버 없음, 그래프 없음, FADING band 없음 — 비어 있으면 무섭기 때문. 따뜻한 bed + seed 예시 + 캡처 어포던스만.

```
+---------------------------------------------------------------------+
|  cold-frame · coby's memory                  [ search... ]  [filter] |
+---------------------------------------------------------------------+
|   🌱  당신의 정원이 막 싹트는 중 — 지금까지 2개 사실.               |
|                                                                     |
|   SEEDLINGS (지금은 전부 새것)                                       |
|    🌱 "coby prefers dark roast"        conf ▓▓▓   src: you · 오늘   |
|    🌱 "coby's main editor is neovim"   conf ▓▓░   src: you · 오늘   |
|                                                                     |
|   ┌─ 이렇게 자랍니다 ────────────────────────────────────────────┐ |
|   │ • Claude Code를 계속 쓰세요 — 일하면서 기억합니다.            │ |
|   │ • 쓰는 사실은 강해집니다 🌱→🌿→🌳. 안 쓰면 흐려집니다.        │ |
|   │ • 무엇도 삭제되지 않습니다 — 흐려진 사실은 shelf에서 기다림.  │ |
|   └──────────────────────────────────────────────────────────────┘ |
|                                                                     |
|   ⊘ 타임-트래블 & 망각은 몇 주의 history가 쌓이면 풀립니다.         |
|     (잠긴 컨트롤은 숨기지 않고 회색 + 이유 표시)                    |
+---------------------------------------------------------------------+
```

**점진 노출 게이트(데이터가 surface를 *벌어야* 함 — 아니면 거짓말):**
- **FADING band**: ≥1 사실이 budding 임계 아래로 decay하면 등장(며칠 미접근 필요). 그 전엔 미표시.
- **as-of 스크러버**: history가 ≥14일 OR ≥1 supersession이 있으면 등장. 그 전엔 회색 + 툴팁("몇 주의 history 필요")으로 표시(숨기지 않음 — 사용자가 기대하게).
- **Triage 큐("결정 대기")**: 엔진이 실제로 `held_for_human`을 flag할 때만. 0건 = 행 전체 숨김(빈 "0 결정" nag 금지).
- **로컬 ego-graph**: Fact Detail에서 ≥1 edge일 때만.
- **band 라벨도 적응**: fresh DB는 **SEEDLINGS**(전부 새것)라 부른다 — decay spread가 없어 EVERGREEN/BUDDING/FADING이 무의미하므로.

**7.1.4 "aha" — 데이터가 처음 임계를 넘을 때.** 14일/첫 supersession 게이트가 트립되면 일회성 부드러운 reveal(유일한 온보딩 토스트이며, *믿음 변화*라 허용됨):

```
  ⟲  타임-트래블이 방금 풀렸습니다.
      이제 당신의 믿음을 되감을 만큼 history가 쌓였습니다.
      해보기: "지난달 coby에 대해 뭘 알았지?"   [보여줘]
```

### 7.2 플로우 2 — 일상 루프: 캡처(암묵) → 회상-영수증 → 제자리 교정(1키) → 교정 확인

이 루프는 **Claude Code 안 MCP에서** 산다. 웹 UI는 일상 surface가 아니다.

**7.2.1 캡처(암묵·조용).** 사용자는 그냥 일한다. Claude Code가 `add_memory`를 호출하면 캡처는 **기본 조용** — 토스트도 방해도 없음(규칙: 토스트는 믿음 변화에만). 확인은 나중 회상-영수증에 산다.

**7.2.2 회상-영수증 — 메모리가 *쓰일 때* WHY와 함께 등장.** `search_memory`가 작업 중 발화하면 모델이 영수증을 인라인 렌더. Margin surface이자 트러스트 코어다.

```
┌─ cold-frame · 이 턴에 3개 사실 회상 ──────────────────────────────┐
│ ● works at Anthropic (since 2026-06)        c:0.95               │
│     from you · "I joined Anthropic" · 2026-06-12   ↩ supersedes   │
│     "works at Vessl"                                             │
│ ◐ prefers pnpm over npm                     c:0.78 · used 6×     │
│     from msg#3f2 · "let's switch to pnpm" · 9d ago               │
│ ○ deploys via Cloudflare edge workers       c:0.62 · fading      │
│     from doc deploy.md · 41d ago — 아직 맞나?                     │
│   ───────────────────────────────────────────────────────────   │
│   [a] accept all   [x] correct…   [w] why (open fact)   [/] mute │
└──────────────────────────────────────────────────────────────────┘
  ● strong   ◐ fading   ○ at-risk
```

규칙: 최대 ~5 사실(unscrollable dump 금지 — ChatGPT 교훈); 각 줄에 한 줄 provenance(kind+ref+age); confidence는 글리프 먼저·소수 둘째(ambient에서 false precision 금지); supersession 히스토리 인라인 표시(엔진이 이미 자가 교정했음을 보임).

**7.2.3 제자리 교정 — 한 키, 되돌릴 수 있음.** `x` → stale 사실 선택 → 진실 입력. 뒤에서: bi-temporal supersession(옛 → archived + invalid_at + supersedes edge + 이 메시지를 원인으로). 옛 사실은 **archived, 절대 삭제 안 함.**

```
  [x] 어느 걸 교정? › 3  deploys via Cloudflare edge workers
  지금 맞는 건? › we moved to Fly.io last week

  ✔ 교정됨. 믿음 분기:
     ~~deploys via Cloudflare edge workers~~  → archived (언제든 Revive)
     ↳ deploys via Fly.io (since ~2026-06-14)   c:0.90  · cause: this msg
```

**7.2.4 교정 확인 — 트러스트를 닫는 순간.** **같은 세션**의 다음 회상이 이미 교정을 반영한다. "저장됨, 재시작해야 보임" 없음. see→trust→correct를 제자리에서 닫는다.

```
┌─ cold-frame · 1개 사실 회상 ──────────────────────────────────────┐
│ ● deploys via Fly.io (since 2026-06-14)     c:0.90               │
│     2분 전 교정함 · was "Cloudflare edge workers"               │
└──────────────────────────────────────────────────────────────────┘
```

그리고 단일 믿음-변화 토스트(유일하게 허용): `⟲ belief updated: Cloudflare → Fly.io`.

**7.2.5 파워유저용 CLI 미러.** Claude Code 없이 같은 루프: `cold-frame recall "deploy"`가 영수증 카드 출력, `cold-frame edit <id>`가 버전 교정. 동일 엔진·동일 provenance — 세 surface, 한 `.db`.

### 7.3 플로우 3 — 주간 큐레이션 + belief-fork 타임-트래블 세션

의도적·~3–5분, `cold-frame ui`에서. 순서: **orient → Triage drain → FADING tend → time-travel로 마무리.**

**7.3.1 Home, populated — 5초 안에 orient.**

```
+---------------------------------------------------------------------+
|  cold-frame · coby's memory          [ search facts… ]   [filter ▾]  |
|  overnight: +6 강화 · 3 병합 · 2 fade   ◀━━●━ as-of TODAY ▶          |
|  active facts: 418  (−3 이번주 — 더 lean)            [⟲ time-travel] |
+---------------------------------------------------------------------+
|  ▸ 네가 필요한 결정 (2)                            [전부 해결 →]     |
|     [!] 충돌: "works at Vessl"  vs  "works at Anthropic"           |
|     [~] 저신뢰 (0.41): "prefers tabs over spaces" — 아직 맞나?      |
+---------------------------------------------------------------------+
|  EVERGREEN (강하고 자주 씀)                          sort: strength ▾ |
|   🌳 "coby prefers dark roast"   S██████ conf▓▓▓ src3 ↔2 used9       |
|   🌳 "main editor = neovim"      S█████  conf▓▓▓ src5 curve╱╲╱╲      |
|  BUDDING                                                             |
|   🌿 "timezone is KST"           S███    conf▓▓  src1                |
|  FADING (돌보거나 보내기)                                            |
|   🌱 "liked teal theme"          S█·   last 71d   [pin] [let go]    |
|   ·  "used pnpm (old proj)"      S·    ~2일 후 archive              |
+---------------------------------------------------------------------+
|  consolidation 매일 밤 · [지금 돌보기] · [⟲ time-travel]            |
```

트러스트 신호: 상단의 **"−3 이번주 — 더 lean"**이 줄어듦을 *승리*로 프레이밍(anti-hoarding), "overnight"이 worker의 행동을 감사 가능하게(silent 아님) 만든다.

**7.3.2 Triage drain — 크고 되돌릴 수 있는 동사만.** `held_for_human` 항목만 등장(나머지는 엔진이 auto-resolve). 각각 한 결정 + 명확한 undo.

```
+-- 결정 1 / 2 ───────────────────────────────────────────────────────+
|  이 둘은 동시에 현재일 수 없음:                                      |
|    A  "works at Vessl"        valid 2025-09 · last seen 2026-04      |
|    B  "works at Anthropic"    valid 2026-06 · src: offer-letter.pdf  |
|                                                                     |
|  [k] B 유지(newer)  [o] A 유지   [b] 둘 다 참   [m] merge            |
|  [p] B pin          [s] 7일 snooze        모든 선택은 되돌릴 수 있음 |
+---------------------------------------------------------------------+
```

규칙: 동사는 크고·명명되고·undoable. "snooze"가 있어 사용자는 결정을 강요당하지 않는다. 해결 시 카운트 갱신 + 항목 fade-out(opacity만 — 리스트 절대 재배치 안 함).

**7.3.3 FADING band tend — Pin/Let-go의 보이는 결과.** fading하지만 중요한 사실 Pin → 망각곡선 **re-spike**, 카드 밝아짐, 글리프 상승 🌱→🌿. Let-go → 흐려지며 "recently faded" shelf로(archived, Revive 항상).

```
   🌱 "liked teal theme"   S█·  last 71d   [pin]→  🌿 S████  (re-spiked)
   ·  "used pnpm (old proj)"          [let go]→  shelf로 fade ⤵ (Revive)

   recently faded (shelf)  ────────────────────────────── [Revive any]
     · "used pnpm (old proj)"   방금 archived   [revive]
```

애니메이션 규칙 enforced: opacity/size만 변경 — **절대 재배치 안 함**(공간 기억 보존, jitter 회피). shelf는 "archive-not-delete"의 보이는 증거이자 단일 최대 트러스트 anchor.

**7.3.4 시그니처 모먼트 — "내가 4월엔 뭘 믿었지?"** `⟲ time-travel` 클릭 → 항상 핀된 스크러버를 과거로 드래그. 뷰 전체가 *재구성*(파라미터화 as-of 쿼리, 녹화 아님). belief-fork가 통과하며 un-correct된다.

```
+--------------------------------------------------------------------+
| ⟲ TIME-TRAVEL    [ 내가 BELIEVED ● | 사실이 TRUE ○ ]               |
| 2024 ───────────[●]──────────────────────────── 2026 (today)       |
|                  ^ as-of = 2025-04-12                               |
+--------------------------------------------------------------------+
|  드래그하며 belief-fork 통과:                                      |
|    was ▸ "works at Vessl"   ̶(̶believed 2025-09 → 2026-06)̶           |
|           │ supersedes  (auto: valid_at newer)                     |
|           ▼ caused_by: "I joined Anthropic" ↗ msg#a17             |
|    now ▸ "works at Anthropic"                                     |
|                                                                    |
|  2025-04-12 기준 당신의 믿음:                                      |
|    🌳 "works at Vessl"   (그땐 현재 · 2026-06 superseded,          |
|                           여기선 다시 살아남)                      |
|    (ghost) "works at Anthropic" — 2026-06까지 당신에게 미지        |
|                                          [오늘로 복귀]            |
+--------------------------------------------------------------------+
```

**두 축 토글이 핵심**: **"내가 BELIEVED"(transaction time)**가 기본이며 당신의 과거 마음(나중에 틀렸다고 밝혀진 믿음 포함)을 보여준다. **"사실이 TRUE"(valid time)**는 지금 아는 한의 ground truth. hover 마이크로카피가 차이를 명시해 bi-temporal 혼동(리서치가 경고한 함정)이 물지 못하게 한다. 스크러버를 놓으면 snap-forward하며 모든 교정을 재적용 — **사용자가 자기 마음이 재교정되는 걸 본다**, 히어로 전체가 한 제스처로.

**7.3.5 루프 닫기.** `[오늘로 복귀]`로 라이브 Home 복귀. 세션은 정원이 보이게 다듬어진 채 끝난다: Triage 0, FADING drain, 조용한 한 줄: `방금 돌봄 · 결정 2건 해결 · 1 pin · 1 let go`.

---

## 8. 비주얼 & 인터랙션 언어

### 8.1 단 하나의 지배 규칙: 한 신호 → 한 채널

엔진은 4개 신호 family를 emit한다. 각각이 전용·비중첩 채널을 받아 사용자가 상태를 pre-attentive하게(파싱이 아니라 글랜스) 읽는다. **한 채널에 두 의미를 절대 안 싣는다**(토폴로지-only 실패 모드).

| 엔진 신호 | 소유 채널 | 절대 안 쓰는 곳 |
|---|---|---|
| **STRENGTH** = f(decay_S, access, last) | opacity(밝기) + size(scale) + 성장 글리프 🌱🌿🌳 + 망각곡선 sparkline | confidence |
| **CONFIDENCE** = 0–1 | discrete ●◐○ + edge/text fuzziness; 소수는 Detail에서만 | strength |
| **STATUS** = active/fading/archived | band 위치 + dim + strikethrough(archived) | importance |
| **EDGE TYPE** = supersedes/relates/derived/mentions | hue + arrow + 필터 칩 | strength |
| **IMPORTANCE/pin** | 📌 마커 + decay "floor"(pin은 🌿 아래로 안 fade) | — |

### 8.2 STRENGTH — 숨 쉬는 신호(차별점)

가장 풍부·중복 인코딩(글리프+밝기+크기+미터+곡선 전부 같은 말 — 중복은 의도적: 글랜스 읽기 + 색맹/grayscale 터미널 생존).

**성장 글리프(파생, 자의 아님, 1:1 검증 가능):**
```
strength ≥ 0.66 ......... 🌳  tree     EVERGREEN band
0.33 ≤ s < 0.66 ......... 🌿  sprout   BUDDING band
0.10 ≤ s < 0.33 ......... 🌱  seedling FADING band
s < 0.10 (archive 임박) .. ·   ember    FADING band, dimmed, "~Nd → archive"
status=archived ......... 🥀  pressed   (recently faded shelf에서만)
pinned .................. 📌  🌿 최소로 floor (decay 무관)
```

**밝기 + 크기(글리프 없는 ambient 읽기):** strength → opacity와 scale에 단조 매핑. **size delta는 작게(최대 12%)** — "evergreen이 크다" gestalt는 느끼되 reflow는 절대 없게. CLI에선 "size"가 미터 fill 길이로 degrade(터미널은 행 scale 불가) — 미터 `S█████`가 cross-surface invariant.

**망각곡선 sparkline(증명):** `access_log`에서 그림(접근=re-spike). 없으면 현재-strength 미터로 degrade. hover 마이크로카피: "각 spike는 이걸 쓴 시점입니다. 사이엔 흐려집니다." 따뜻하게, 절대 "retrievability decay coefficient" 아님.

### 8.3 CONFIDENCE — discrete, 거짓 정밀 금지

confidence는 epistemic(사실이 얼마나 확실한가), strength(얼마나 살아있나)와 직교. 🌳-강하지만 ◐-불확실 가능.
```
●  sure       c ≥ 0.80    solid, crisp
◐  likely     0.50–0.80   half, slightly soft
○  unsure     c < 0.50    hollow, fuzzy text + faint outline
```
ambient fuzziness(웹): `○` 사실은 *사실 텍스트 자체*에 `blur(0.3px)` + 낮은 대비 — 불확실성을 읽기 전에 느낀다. CLI는 blur 불가 → `○`는 dim/italic + 글리프가 의미를 운반. 소수는 **Fact Detail 헤더 한 곳에서만** 정직하게: `"prefers tabs" ○ unsure · conf 0.41 [why so low? ▾]`. **절대 `c:0.7384` 금지** — Detail에서 2dp, 나머지는 글리프.

### 8.4 STATUS — 배지가 아니라 band 위치

```
EVERGREEN  (active, strong)    full opacity, normal flow
BUDDING    (active, mid)       full opacity
FADING     (active, weak)      60–42% opacity, [pin][let go] 어포던스 여기
─ recently faded ─ (archived)  30% opacity, 🥀, strikethrough, [revive] 항상
```
archived는 절대 제거 안 됨. collapsed "recently faded" shelf로 DOWN 슬라이드(dim + struck + 영구 `[revive]`). 뷰에서 지우기 = opacity fade, 노드 제거 아님(공간 기억 + immutable 규칙 보존).

### 8.5 EDGE TYPE — 네 채널, 네 칩

```
supersedes    ⤳  clay/amber    "replaced by → / was"   (belief-fork 채널 — 가장 큼)
relates_to    —  moss/teal     "relates to"            (중립, 얇음)
derived_from  ⊢  bark/violet   "summarized from"       (consolidation 출력)
mentions      ·  paper/gray    "mentions"              (가장 희미, 점선)
```
필터 칩(웹 헤더 + CLI `show --edges` legend): `[⤳ supersedes 3] [— relates 8] [⊢ derived 2] [· mentions 14]`. 기본: mentions OFF(가장 noisy), supersedes 항상 ON(히어로 스토리). weight → 선 두께(웹) / 글리프 반복(CLI: `——` > `—`).

### 8.6 색 & 타이포 (따뜻함, clinical 아님)

**팔레트 — "정원 돌보기" 흙빛(파랑 대시보드 아님):**
```
                LIGHT (paper)        DARK (loam)
bg base         #FAF6EE paper        #1A1714 loam
bg raised       #FFFFFF              #241F1A bark-dark
text primary    #2E2A24 bark         #ECE4D6 cream
text dim        #8A7F6E              #8C8070
moss   (relates/positive/strong)     #5C7A52 / #7FA06E
clay   (supersedes/belief-change)    #B5683C / #D08A5A
amber  (fading/needs-tending)        #C99A3E / #E0B85C
violet (derived/consolidation)       #6B5B8A / #9C8AC0
at-risk(○ unsure/conflict)           #A6603A muted clay (절대 #FF0000 금지)
```
**규칙: 알림은 WARM(clay/amber = "당신의 돌봄 필요"), 절대 clinical red/green 아님.** 망각은 "error"가 아니라 "needs tending". 성공은 green 체크가 아니라 *더 찬 미터 + 더 높은 글리프*.

**타이포 & 밀도:** UI = humanist sans 하나(Inter/system-ui, 사실은 문장처럼 prose로 읽힘). mono 하나(JetBrains/SF Mono)로 미터·id·timestamp·sparkline·**모든 CLI** — `S█████`가 브라우저/터미널에서 픽셀-동일 컨셉. 밀도는 comfortable(사실 1개=생각 1개, 행 ≤2줄, line-height 1.5) — 훑는 스프레드시트가 아니라 거니는 정원.

### 8.7 모션 — 세 철칙

1. **opacity + size만, 절대 reposition 안 함.** Pin/let-go/decay/as-of-scrub 전부 제자리에서 밝기·scale 애니메이트. 재정렬은 *명시적 사용자 sort 변경*에서만, fly-around가 아니라 cross-fade로.
2. **토스트는 믿음이 변할 때만**(supersession/conflict). add/read/reinforce엔 절대. 토스트 카피는 상태가 아니라 스토리: *"Updated: 이제 Anthropic 근무 (was Vessl). [why] [undo]"*.
3. **archive는 down으로 fade, out 아님.** Let-go = opacity 100→30% + shelf로 collapse, 240ms ease. Revive가 역재생. 무엇도 존재에서 pop하지 않음.
타이밍: 상태는 180–240ms ease-out. Pin의 곡선 re-spike는 sparkline이 ~400ms 동안 차오름(유일한 "delight" beat — 살아 돌아오는 걸 본다). `prefers-reduced-motion` → cross-fade만, scale 없음.

### 8.8 회상-영수증 카드 (Margin/MCP/`recall`) 스펙

cross-surface anchor. MCP 출력과 `cold-frame recall`에서 동일. 순수 텍스트라 어디서나 ship. 같은 ●◐○ 글리프.

```
┌─ cold-frame · 이 턴에 4개 사실 회상 ───────────────────────┐
│ ● deploys via Cloudflare edge workers      c0.91 · 5d     │
│   ⊢ why: msg#3f2 "moved auth to edge"                     │
│ ◐ prefers pnpm over npm                     c0.78 · used6×│
│ ○ works at Anthropic (since 2026-06)        c0.95         │
│   ⤳ supersedes "works at Vessl"                           │
│   ── tend ─────────────────────────────────────────────  │
│   [a]ccept all   [x]correct   [w]hy ▸detail   [/]mute     │
└────────────────────────────────────────────────────────────┘
  ● sure  ◐ likely  ○ unsure       (footer legend 항상 present)
```
규칙: 최대 5 사실; 각 줄 = 글리프 + 사실 + `c0.NN` + freshness; provenance는 자체 indented 줄에 edge 글리프(⤳/⊢) prefix; belief-change(⤳)는 5 초과해도 항상 표시; footer legend 항상.

### 8.9 CLI 비주얼 — 같은 글리프 (parity)

CLI와 웹은 한 프로그램. 글리프 어휘(🌱🌿🌳 ●◐○ ⤳—⊢· 📌)와 `S█████` 미터가 바이트-동일. rich로 색을 쓰되 모든 신호가 글리프도 가져 `--no-color`/파이프에서 legible. 이모지 폭 문제 대비 `--ascii` 폴백 세트(`^ ~ T x *` 등) 제공.

```
 cold-frame · what I know about coby            ◀━●━ as-of TODAY
 active 418  · -3 this week (leaner)  · 2 need you →

 EVERGREEN
  🌳 ● coby prefers dark roast       S██████  ↔2  used9  ▁▃▆█▆▄
 BUDDING
  🌿 ◐ timezone is KST               S████··  src1 used2  ▃▄▅
 FADING  · 돌보거나 보내기
  🌱 ○ liked teal theme              S█·····  71d  [pin] [let go]
 ─ recently faded (3) ─  [revive]
  🥀  ̶u̶s̶e̶d̶ ̶Y̶a̶r̶n̶ ̶c̶l̶a̶s̶s̶i̶c̶      archived 12d ago
```

### 8.10 렌더링 contract (state matrix)

| 상태 | 글리프 | conf | 미터 | opacity | band | 어포던스 |
|---|---|---|---|---|---|---|
| strong+sure | 🌳 | ● | S██████ | 100% | EVERGREEN | reinforce |
| strong+unsure | 🌳 | ○ | S█████· | 100%(text fuzzy) | EVERGREEN | confirm? |
| fading+sure | 🌱 | ● | S█····· | 60% | FADING | pin/let go |
| fading+unsure | 🌱 | ○ | S······ | 50%(fuzzy) | FADING | tend |
| pinned+fading | 📌🌿 | ● | S███··· | 85% | BUDDING(floored) | unpin |
| archived | 🥀 | — | S······ | 30% struck | shelf | revive |
| superseded(fork) | ⤳🥀 | — | — | 30% struck | history | revert |

어떤 surface(웹/CLI/MCP)든 같은 엔진 신호에서 같은 행을 렌더한다 — 이게 contract다.

---

## 9. 상태 & 엣지 케이스

### 9.1 모든 상태를 지배하는 두 원칙

1. **기본적으로 reversible.** 파괴적으로 보이는 모든 행동(let-go/merge/auto-archive/edit/resolve)이 ~10초 인라인 Undo + 영구 Revive를 노출한다. **"delete"는 사용자 UI에 절대 안 나온다** — *let go / archive / retire*라 한다. `status='deleted'`는 엔진 내부일 뿐 primary 동사로 표면화 안 됨.
2. **empty ≠ broken; big ≠ bloated.** cold-start = "아직 안 심은 정원", scale = "lean하고 tended". raw 카운트를 지배 감정 신호로 안 씀 — 항상 *방향*과 짝지음("418 active · −3 이번주, leaner").
3. **모든 전이는 opacity/size만**(skeleton 포함 — skeleton은 제자리 fade, 슬라이드 안 함).
4. **토스트는 믿음 변화에만**(supersession/conflict/pin-인접 auto-archive). load/scroll/filter/일반 저장은 조용.
5. **keyless/offline baseline으로 degrade, 절대 blocking modal로 안 감.**

### 9.2 Home — 4 라이프사이클 상태

**Cold-start (0):** band 리스트를 single warm planting 카드로 교체. 스크러버 disabled(회색 + 툴팁). "Nothing planted yet." / "cold-frame는 당신(과 Claude)이 말하는 것에서 배웁니다." Primary `[+ Add a fact]`, secondary `[Connect to Claude Code]`, tertiary `[Import…]`.

```
+---------------------------------------------------------------------+
|  cold-frame · coby's memory     [ search facts... ] [filter v]        |
|  active facts: 0         ⊘ as-of: 며칠의 메모리가 필요  ◐            |
+---------------------------------------------------------------------+
|                          🌱                                         |
|                  Nothing planted yet.                               |
|   cold-frame는 당신(과 Claude)이 말하는 것에서 배웁니다.             |
|   첫 사실을 추가하거나, 일하면서 저절로 자라게 두세요.              |
|                                                                     |
|        [ + Add a fact ]   [ Connect to Claude Code ]   [ Import… ]  |
|                                                                     |
|   band·망각곡선·타임-트래블은 메모리가 무르익으면 나타납니다.       |
+---------------------------------------------------------------------+
```

**Near-cold (1–9):** 실제 리스트지만 band 헤더 억제(4 사실 banding은 우스움) — 전부 🌱 flat 리스트, footer "band와 곡선은 메모리가 무르익으면 나타납니다." Triage 섹션은 진짜 항목 전엔 없음.

**Loading:** skeleton 카드가 제자리 fade(글리프/미터 칼럼 shimmer). 헤더 카운트는 `· · ·`(0 아님). overnight ribbon "checking what changed…" **빈 상태 → populate flash 절대 금지**(고장처럼 보임). ~400ms 넘으면 skeleton 유지, ~5s 넘으면 slow/error 경로.

**Error (3종, generic "뭔가 잘못됨" 금지):**
- **DB busy**(다른 프로세스 write 중): non-blocking amber ribbon "Memory is busy (Claude is writing) — retrying…" + backoff 자동 재시도, 자동 clear. modal 없음. read 뷰는 last-known 렌더.
- **DB missing/corrupt**: full-region 카드 "메모리 파일을 열 수 없습니다." 경로 표시, `[Run doctor]` `[Restore from backup…]` `[Open folder]`. 안심 카피: "데이터는 사라지지 않았습니다 — 파일이 안 열렸을 뿐. doctor가 점검합니다."
- **Embedder/LLM 불가**: 에러 아님 — 얇은 persistent 배너(9.4). Home은 완전 사용 가능.

**Scale:**
- **~10:** flat/lightly banded, full 글리프+곡선, virtualization 없음.
- **~1k:** virtualized 리스트(고정 행 높이 → scrollbar 안정 = 공간 기억). band collapsible + 카운트(`EVERGREEN (212) ▸`). sparkline은 viewport 행만 lazy.
- **~10k+:** band 기본 collapse → 카운트 + 1줄 peek(top 3 by strength). search/filter가 primary 내비. persistent hint "10k+ facts — search or filter; 전부 스크롤은 길이 아님." sort는 server-side(SQL). 헤더 reframe: "8,940 active · 지난달 1,200 consolidated, staying lean."

### 9.3 Fact Detail — 4 상태

**Cold/empty-ish(새 사실):** belief-history → "No corrections yet — this is the original belief." ego-graph → "No connections yet"(빈 캔버스 아님). 곡선 → seed 점 1개 + "곡선은 몇 번 회상되면 나타납니다." history 필요한 건 전부 한 줄로 degrade, 빈 차트 프레임 절대 금지.

**Loading:** 헤더(content/conf/글리프)가 navigate해 온 Home 행에서 먼저 렌더(flash 없음). provenance/history/ego-graph는 아래에서 lazy + 제자리 skeleton. 사실은 "pop in" 안 됨 — Home 행에서 연속(size 애니메이션만).

**Error:**
- **source 못 엶**(파일 이동/chat 사라짐): 인라인 "Source unavailable — 원본(`offer-letter.pdf`)이 이동/삭제됨. 사실과 hash는 보존." 행 유지, `[open]` strike, `[copy ref]` 제공. **provenance 무결성: `observed_at` + `content_hash` 표시 유지** — 파일이 사라져도 살아남음.
- **ego-graph 계산 실패**: 섹션을 "Connections unavailable [retry]"로 collapse — 나머지 절대 block 안 함.

**Scale(promiscuous-hub):** 긴 belief history → 최신 3 fork 펼침, 나머지 "▸ 4 earlier corrections". 많은 source(10+) → 최신 3 + "▸ 12 more". **ego-graph HARD CAP 12 이웃**(edge weight × neighbor strength 랭킹) + "+38 more connections" muted 칩.

```
+-- FACT --------------------------------------------------------------+
| "coby uses a computer"  ⚡ conf ▓▓░  🌳?  access×61                  |
| ⚡ High-traffic memory — 47개와 연결됨. 과연결 메모리는 종종           |
|    여러 개여야 할 한 사실을 뜻합니다.                                |
|                              [ Split… ]  [ keep as-is ]  [ let go ] |
| ── provenance ───────────────────────────────────────────────────  |
|   • doc  offer-letter.pdf  2026-06-12   source unavailable (moved)  |
|        kept: hash 4f3a… · observed 2026-06-12   [copy ref]          |
|   ▸ 12 more sources                                                 |
| ── nearby (1–2 hop) ──── filter ▸ type ▸ conf ▸ ──────────────────  |
|   relates_to→ "prefers neovim"   relates_to→ "based in SF"          |
|   … (showing 12 of 47)   [ +35 more connections ]                  |
| [pin] [edit] [reinforce] [split] [let go]                           |
+----------------------------------------------------------------------+
```

### 9.4 제품 엣지 케이스

**C. Offline / no embedding model** (HashEmbedder = *기본*이라 의도적으로 느껴야 함, degraded 아님). Home에 neutral 배너: "Offline mode — search uses keywords + hashing. 더 나은 recall은 embedding model 추가. [Set up] [Dismiss]." 검색 작동(BM25 + hash KNN), 라벨만. 결과에 tiny `~` 글리프(approximate). add/search 절대 block 안 함. *이전에 더 나은 embedder가 설정됐다 unreachable*(예: OpenAI 키 폐기)이면 진짜 warning: "embedding model(OpenAI) unreachable — offline fallback. offline 색인 사실은 복구 시 재색인. [Why?] [Retry]."

**D. `claude` CLI absent (setup)**: 절대 hard-fail 안 함. 없으면 `.mcp.json` write/merge + copy-paste 블록 출력. "`claude` 명령을 못 찾아 MCP를 수동으로 설정. cold-frame는 혼자서도 작동(CLI+웹 UI). Claude Code 연결 마무리: 1) Claude Code 설치, 2) `cold-frame setup` 재실행, OR 3) 아래 config가 이미 `.mcp.json`에." **Exit 0**(note 있는 성공), 실패 아님. doctor는 yellow "Claude Code: not linked (optional)", 절대 red 아님.

**E. 진짜 모순(사용자 필요)**: 엔진은 freshness(valid_at)로 결정적 해결하며 그것엔 안 물음. **freshness가 진짜 모호할 때만**(같은/미상 valid_at) Triage에 first-class 카드로. 토스트는 *존재만 알림*("A belief needs you"). 양쪽 claim + provenance side-by-side, 동사 `[Keep new]` `[Keep old]` `[Both true]` `[Neither/edit]` `[Snooze]`. 해결 전까지 **둘 다 active-but-flagged**(silent pick 안 함), 회상-영수증에 `[!]`로 인라인 표면화.

```
+--------------------------------------------------------------------+
|  > a belief needs you                                  [snooze all]|
+--------------------------------------------------------------------+
|  [!] 두 가지가 disagree, 어느 게 newer인지 모름:                    |
|   A) "coby works at Vessl"        B) "coby works at Anthropic"     |
|      src: chat 2025-09 (user)        src: offer-letter.pdf (undated)|
|   [ Keep B (new) ]  [ Keep A (old) ]  [ Both true ]               |
|   [ Neither / edit ]                  [ snooze ]                   |
|   뭘 고르든 reversible — belief-fork를 써서 나중에 rewind 가능.     |
|   고를 때까지 둘 다 유지(flagged).                                 |
+--------------------------------------------------------------------+
```

**F. Promiscuous junk hub**: SPEC down-weight가 de-rank하고, UI는 *보이고 고칠 수 있게* 긍정 프레이밍. Home에 distinct ⚡ + Tend nudge "47개와 연결 — 너무 broad할 수 있음. [Look] [Split] [Let go]." Detail에서 12 cap + 배너 "High-traffic memory — 과연결은 종종 여러 사실이어야 할 하나를 뜻함." `[Split]`은 assisted breakup(내용에서 원자 사실 제안). 절대 auto-delete 안 함 — Tend 기회.

**G. Disputed auto-archive(사용자가 망각에 반대)**: 망각은 기능이라 *보이고·합의되고·reversible*(ChatGPT anti-pattern 회피). 3겹 안전: (1) **Pre-archive** — FADING band에 "~2일 후 archive" 카운트다운, `[pin]`으로 곡선 re-spike & archive 취소. (2) **At archive** — 아침 "overnight: 2 faded" ribbon 클릭 → 무엇을·왜 archive했는지 ledger(저-score 분해), 각 `[Revive]`. pin-인접 archive는 유일한 auto-토스트 "Archived 'used pnpm' (near a pinned fact) — [Revive] [Why?]." (3) **Forever** — "Recently faded" shelf, 항상 `[Revive]`, `--status archived`로 검색 가능. **Revive는 status=active + decay re-spike**(즉시 재-archive 방지). 트러스트 라인: "무엇도 삭제 안 됨 — let-go 메모리는 여기서 쉬고 돌아올 수 있음."

```
+--------------------------------------------------------------------+
|  ⟲ overnight ledger · 2026-06-21                      [ close ]     |
|  당신이 떠난 사이 메모리가 한 일:                                   |
|  +6 강화   3 병합   2 fade                                          |
|  faded (archived, not deleted):                                    |
|   · "used pnpm (old proj)"   score .08  (last 94d, importance↓)    |
|        [ Revive ]  [ why? ]                                        |
|  무엇도 삭제 안 됨 — let-go 메모리는 Recently-faded shelf에서 대기. |
+--------------------------------------------------------------------+
```

**H. Ambiguous merge**: dedup이 확신 못하면 auto-merge 안 함 — Triage merge 카드(overlap 하이라이트 + distinct provenance). `[Merge → keep best wording]`(결과 사실 preview), `[Keep separate]`(`relates_to` 기록 → 재질문 멈춤), `[Snooze]`. merge는 reversible(양 source-set + note_history 보존, Fact Detail에 `[Unmerge]`). "These might be the same memory. Merge, or keep both?"

**I. Empty/sparse as-of**: 최초 메모리 이전으로 스크럽 → 제자리(에러 아님) "Nothing known yet at Apr 2024 — your memory begins June 2026." as_of에 아직 안 믿은 사실은 muted ghost "(unknown until 2026-06)". 스크러버 draggable range를 **[earliest_created, today]로 clamp** + soft bumper — 실수로 void에 스크럽 못 함.

### 9.5 상태 와이어프레임 — Home + Fact Detail 요약

```
Home    : Cold-start(planting 카드) → Near-cold(flat 🌱) → Loading(skeleton ···)
          → Mature(EVERGREEN/BUDDING/FADING + 스크러버) → DB busy(amber ribbon)
          → Scale 10k+(collapsed band + search-first)
Fact    : empty(history/edge 한 줄) → loading(Home 행 연속) → source missing(hash 보존)
          → promiscuous hub(⚡ + 12 cap + Split)
```

이 상태들은 기존 스키마(status, note_history, sources, edges, decay 컬럼, access_log)를 재사용하며, 순수 net-new UI 요구는 둘뿐이다: 엔진의 `held_for_human` flag 노출(Triage)과 archive ledger용 low-score breakdown.

---

## 10. 다음 디자인 결정 (열린 것)

구현 전에 닫아야 할 구체적 UX 결정:

1. **공유 쿼리 wrapper 강제** — 모든 섹션의 데이터 레이어가 `?as_of/?lens/?scope`를 honor하도록 단일 wrapper 정의. 한 섹션이 무시하면 chrome은 과거인데 데이터는 현재를 보여주는 bi-temporal 혼동(트러스트 파괴)이 발생.
2. **Cold deep-link fallback** — Home 없이 `/fact/:id`로 직접 진입(CLI/MCP에서) 시: dimmed Home을 뒤에 렌더할지, "back to Home" rail 상태를 가진 full-page detail로 할지 확정.
3. **과거 시점의 destructive 동사 정책** — as_of가 과거일 때 let go/merge 등 큐레이션 동사를 disable할지 warn할지(과거 믿음에 실수로 행동 방지). persistent "viewing past" 칩의 unmissable 디자인.
4. **두 축 토글 usability** — "BELIEVED vs TRUE"는 개념적으로 어려움. hover 카피로 충분한지, 기본값/시각 차별을 usability test로 검증.
5. **점진 노출 임계값 sync** — FADING(≥1 decayed) / 스크러버(≥14일 or ≥1 supersession) / Triage(≥1 held_for_human) / ego(≥1 edge) 게이트가 엔진이 쓰는 동일 임계값과 동기화되는지(라벨이 데이터와 모순 안 나게). 14일 게이트가 실제로 impressive한 지점인지 튜닝.
6. **Triage 진입 기준** — 엔진이 *대부분* auto-resolve하고 *소수*만 surface하도록 게이트(너무 많이 held_for_human이면 주간 세션이 chore = Greenhouse 실패 모드). null/undated valid_at의 sane tie-break 정의.
7. **virtualized 행 높이** — 고정 높이(긴 사실/펼친 fork truncate 위험) vs 가변 높이(scrollbar jitter = 공간 기억 파괴) 사이 measured/estimated-height windowing 결정.
8. **ego-graph cap & hub flag 임계** — 12 이웃 cap과 "suspiciously busy"(예: 47 connections) 임계를 실제 degree 분포가 생기면 튜닝.
9. **접근성 토큰** — warm 팔레트(paper 위 amber, ○-unsure 저대비+blur)가 WCAG AA 통과하도록 verified-contrast 토큰 + high-contrast 테마 override. `○` blur는 ≤0.3px + 설정 gate(rendering bug/난독 우려).
10. **CLI↔웹 미터/글리프 snapshot test** — block char rounding, 256-color 깊이, 이모지 double-width 차이가 "almost but not quite same"(명확히 다른 것보다 나쁨)이 안 되게 공유 spec + snapshot test. `--ascii` 폴백 세트 확정.
11. **암묵 캡처 가시성** — silent 캡처라 회상이 한 번도 안 트리거된 세션은 저장이 안 보임 → 주기적 "recently learned" digest를 둘지 결정.


---

# Part 3: 비주얼 디자인 언어 — Dark Minimal + Life (D14, 미학 단일 출처)

> 방향(사용자 결정): **Linear의 정밀함 + Arc의 생동감.** 다크 우선·미니멀하되 메모리가 *살아있게*. **이 Part가 비주얼 미학의 단일 출처** — Part 2의 "garden 따뜻함 / light 기본" 톤 언급보다 우선한다(기본 테마 = DARK). 컨셉(살아있는 메모리)은 유지하되 실행은 California-startup 미니멀 폴리시.

## 8.1 컬러 (다크 우선 토큰)
- **canvas** near-black `#0B0C0E~#111317` (순흑 회피, 미세한 따뜻함) · **surface/elevated** `#16181D` · **border** hairline `#FFFFFF14`
- **text** high `#ECEDEE` / mid `#9598A1` / low `#5A5D66`
- **accent**(시그니처 1색, CTA·as-of·강조에만) iris/electric `#7C5CFF`~`#6E7BFF` — cold-frame 정체성색으로 확정 예정
- **상태색**: strong=accent glow · fading=text-low로 dim · at-risk/conflict=amber `#F5A623` · archived=거의 비가시
- 라이트 테마는 옵션(반전). **다크가 정체성.**

## 8.2 강도/decay = 빛 (핵심 혁신 비주얼)
- strong 기억 = 높은 대비 + 미세한 accent **glow/halo** · fading = opacity↓ + 채도↓ → 말 그대로 *흐려짐* · archived = 그림자만
- forgetting-curve = 1px sparkline, 사용 시점마다 작은 점 **re-spike**
- decay는 *움직임이 아니라 빛/투명도*로 표현(reposition 금지 규칙과 일치)

## 8.3 타이포 (Geist/Inter 계열)
- UI sans = Inter/Geist Sans · 사실 content는 약간 큰 가독 크기 · **숫자/메타/id/timestamp/strength bar = mono**(Geist Mono/JetBrains)
- 위계는 큰 굵은 헤딩이 아니라 **크기·색 대비**로(미니멀)

## 8.4 스페이싱/밀도/컴포넌트
- 8px 그리드, 넉넉한 여백이되 정보 밀도 유지(Linear식 dense-yet-clean)
- 카드 = 한 줄 사실 + strength 미터 + 메타 인라인 · hairline divider · 호버 시 미세 elevation+glow
- segmented control(BELIEVED/TRUE), 미니멀 트랙 슬라이더(as-of), command-palette(⌘K 검색)

## 8.5 글리프 재정의 (이모지 → 절제)
- 성장 단계 🌱🌿🌳 → **3단 미니멀 인디케이터**(채워지는 leaf/diamond outline 또는 strength-bar 색·glow). Arc식 생동감 *약간*만, 절제.
- 상태 dot ●strong ◐fading ○at-risk 유지(미니멀·일관)

## 8.6 모션 (절제된 마이크로인터랙션)
- **opacity/size/glow만**(reposition 금지), 200–300ms ease
- belief-fork/as-of 드래그 = 프레임당 재구성 + 부드러운 cross-fade, 새 사실 highlight glow / 옛 사실 strike+dim
- pin → re-spike 짧은 glow pulse · let-go → fade out to shelf · 토스트는 *믿음 변할 때만*, 다크 surface에 subtle

## 8.7 시그니처 히어로 비주얼
- **as-of 스크러버**: 상단 중앙 미니멀 트랙+핸들, 끌면 전체가 그 시점으로 재구성
- **belief-fork**: 다크 캔버스에서 옛(dim+strike) → accent 화살표(glow) → 새(bright). 타임-트래블이 시각적으로 가장 아름다운 순간 = 제품의 "wow"

## 8.8 CLI ↔ 웹 일관성 / 접근성
- CLI(다크 터미널)도 같은 의미색(accent=강조, dim=fading) + 같은 글리프 + 같은 strength 문자(▓░) → "같은 제품"
- WCAG AA 대비(다크 text-high 충분), 색만으로 상태 전달 금지(글리프+텍스트 병행), prefers-reduced-motion 지원

---

## 8.9 UI 구현 체크리스트 — Vercel Web Interface Guidelines 검증 (antfu/skills)

> 우리 UX 계획(Part 2·3)을 Vercel Web Interface Guidelines 13개 카테고리에 대조. **결론: 아키텍처적으로 중요한 규칙에 이미 강하게 수렴 = 계획이 탄탄하다는 신호.**

### (a) 이미 부합 — 계획 견고 증거 (독립적으로 프로 가이드라인에 수렴)
- **URL이 상태 반영 + stateful UI deep-link** (가이드: query params/nuqs) ← Part2 §6.2 (as_of/lens/scope permalink)
- **빈 상태 우아하게** ← Part2 cold-start (안 사라지는 섹션 + 설명형 empty-state)
- **파괴적 동작=확인/undo** ← archive-not-delete + always Revive + Ledger Undo
- **transform/opacity만 + prefers-reduced-motion + interruptible** ← Part3 §8.6 (reposition 금지, as-of 드래그 interruptible)
- **대비 + 색만으로 상태 전달 금지** ← Part3 §8.8 (글리프+텍스트)
- **>50 항목 가상화** ← Part2 states (10/1k/10k 스케일, list virtualization, ego cap)
- **다크모드 우선** ← D14

### (b) 지금 격상 — 우리 앱 핵심이라 미루지 말 것
- **Intl.DateTimeFormat / NumberFormat**: 앱이 시간 중심(as-of·"5d ago"·forgetting curve) → 하드코딩 금지, 로케일 포맷.
- **animation interruptible + transition 속성 명시**(`transition:all` 금지): as-of 스크러버/belief-fork.
- **긴 콘텐츠 처리**: 사실 길이 다양 → `truncate`/`line-clamp`/`break-words` + flex `min-w-0`.
- **`color-scheme: dark` + `<meta theme-color>`**: 다크 우선 정합.
- **`aria-live="polite"`**: 믿음-변화 토스트/검증 메시지.

### (c) 빌드 체크리스트 — 코딩 시 (구현 디테일, 정상적 defer)
- a11y: icon 버튼 `aria-label`, form `<label>`, 키보드 핸들러, `<button>`/`<a>` 시맨틱, 계층 heading + skip link
- focus: `:focus-visible` ring, `outline-none` 단독 금지 (키보드 우선과 정합)
- forms: `autocomplete`/`inputmode`, paste 차단 금지, 인라인 에러+첫 에러 포커스, 제출 버튼 enabled+스피너
- typography: `…`/곡선따옴표, 숫자열 `tabular-nums`(strength 미터/카운트), heading `text-wrap: balance`, `⌘ K` non-breaking space
- touch: `touch-action: manipulation`, drawer `overscroll-behavior: contain`, 드래그 중 text-select 비활성
- copy: 능동태, 구체적 버튼 라벨, 카운트 숫자, 에러에 다음-단계 포함

### 미결 — 이 검증이 드러낸 진짜 갭
- **웹 UI 프런트엔드 스택 미결정**: SPEC §9는 "경량 ASGI + 정적 페이지"까지만. 가이드라인(nuqs·virtua·focus-visible:ring)은 모던 프런트엔드 전제. **결정 필요**: Vite+Vue+UnoCSS(antfu/skills가 커버) vs React+Next vs 서버렌더 Python. 다크-미니멀+가상화+deep-link 요구엔 **Vite 기반 SPA**가 적합.
- 범위 주의: 이 검증은 **UI 차원**만. 엔진/전략 견고성은 앞선 적대적 리뷰(build-vs-use·언어 critique 등)로 검증됨.
