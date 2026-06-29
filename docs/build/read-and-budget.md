# Read Path (retrieve → RRF → rerank → token-budget packer → reinforce) + offline token counting + temporal edge filtering

> where_it_goes: New focused doc `docs/spec/read-path.md`, referenced from SPEC §5 (replace the 6-line pseudocode block with a pointer). Also folds three audit fixes into SPEC: (a) §6/§8.5 vs ux §8.2 strength-band reconciliation, (b) access_log DDL + write trigger + retention into design.md §2.3, (c) embedding-dimension handling note into §3/§1.

## SPEC §5 — Read Path (BUILD-READY)

> Scope of this doc: the full `search()` read pipeline — retrieve → fuse(RRF) → (optional) rerank → token-budget pack → reinforce — as mechanical pseudocode with exact signatures, constants, data flow, edge cases, and the offline token counter. Resolves audit findings: strength-band/archive-score reconciliation (§6/§8.5 vs ux §8.2), `access_log` DDL+write+retention, embedding-dim handling, temporal filtering on edges. Lives in `cold_frame/read/{retrieve.py,fuse.py,rerank.py,budget.py}` + `cold_frame/llm/tokens.py`.

---

### 5.0 Entry signature & result types

```python
# cold_frame/api.py  (Memory facade)
async def search(
    self, query: str, *, scope: Scope, k: int = 10,
    token_budget: int | None = None, as_of: datetime | None = None,
    include_archived: bool = False, rerank: bool = False,
    explain: bool = False,
) -> SearchResult: ...

# cold_frame/models.py
class Signals(BaseModel):           # per-hit provenance of the ranking (recall-receipt)
    rrf: float                      # fused RRF score (primary sort key pre-rerank)
    semantic: float | None = None   # cosine ∈ [-1,1] if note appeared in KNN list
    bm25: float | None = None       # sigmoid-normalized ∈ [0,1] if matched FTS
    edge_boost: float = 0.0         # additive RRF contribution from edge channel
    rerank: float | None = None     # cross-encoder/LLM score if rerank=True
    meta_boost: float = 1.0         # recency/scope multiplier actually applied
    strength: float                 # display S (§6 canonical formula), recomputed at read
    rank_in: dict[str, int]         # {"semantic":3,"bm25":1,"edge":7} 0-based ranks per channel

class Hit(BaseModel):
    note: Note
    score: float                    # final score used for ordering (rrf or rerank-adjusted)
    signals: Signals
    truncated: bool = False         # set by packer if content was cut to fit budget
    packed_content: str             # content actually emitted (== note.content unless truncated)
    tokens: int                     # token count of packed_content under active counter

class SearchResult(BaseModel):
    hits: list[Hit]
    used: int = 0                   # sum of hits[].tokens (0 if no budget given)
    budget: int | None = None
    dropped: int = 0                # candidates ranked but excluded by budget cap
    counter: str                    # "tiktoken:cl100k_base" | "heuristic-chars4"
```

`k` is the **final** result count (post-fusion, pre/post-budget). Over-fetch factor `FANOUT = 4` (per-channel candidate count = `k * FANOUT`, min 20, capped at 200).

---

### 5.1 Pipeline (top-level, deterministic)

```python
async def search(query, scope, k, token_budget, as_of, include_archived, rerank, explain):
    statuses = ("active",) if not include_archived else ("active", "archived")
    # quarantined notes (held_for_human/provenance-less) are NEVER in default search:
    #   the FILTER below adds `AND quarantined = 0` (see §5.2). include_archived does NOT
    #   surface quarantined; only the Triage queue (§6) reads quarantined=1.

    cand_k = max(20, min(200, k * FANOUT))                      # 5.2 FAN-OUT width

    # --- FAN-OUT (parallel; each returns ranked list of (note_id, raw_score)) ---
    sem_task  = store.knn(query_emb, cand_k, scope, statuses, as_of)     # 5.3
    bm25_task = store.bm25(query, cand_k, scope, statuses, as_of)        # 5.4
    sem_list, bm25_raw = await gather(sem_task, bm25_task)

    edge_list = edge_channel(sem_list, bm25_raw, scope, statuses, as_of) # 5.5 (sync, cheap)

    # --- normalize per channel into ordered id-lists for RRF ---
    sem_ids  = [nid for nid,_ in sem_list]                              # already cosine-desc
    bm25_norm = normalize_bm25_scores(query, bm25_raw)                  # 5.4 -> {id: [0,1]}
    bm25_ids = [nid for nid,_ in sorted(bm25_norm.items(),
                                        key=lambda kv: kv[1], reverse=True)]
    edge_ids = [nid for nid,_ in edge_list]                            # promiscuity-downweighted

    # --- FUSE: RRF over the three rank-lists (5.6) ---
    fused = rrf_fuse(
        channels={"semantic": sem_ids, "bm25": bm25_ids, "edge": edge_ids},
        k_const=60,
        edge_weight_fn=lambda nid: edge_weight_map.get(nid, 1.0),       # 5.5 downweight
    )                                                                   # -> [(id, rrf_score, rank_in)]

    # --- hydrate notes (single get_notes; preserves order) ---
    note_map = {n.id: n for n in store.get_notes([nid for nid,_,_ in fused])}
    hits = build_hits(fused, note_map, sem_scores, bm25_norm, edge_weight_map, as_of)

    # --- RERANK (optional, default off) (5.7) ---
    if rerank:
        hits = await rerank_hits(query, hits, scope)                   # mutates score, signals.rerank
    else:
        hits = apply_meta_boost(hits, query)                           # recency/scope only (5.7)
    hits.sort(key=lambda h: h.score, reverse=True)

    # --- truncate to k BEFORE budget (budget packs within these k) ---
    hits = hits[:k]

    # --- BUDGET pack (5.8) ---
    if token_budget is not None:
        hits, used, dropped, counter = pack_budget(hits, token_budget)
    else:
        for h in hits: h.packed_content, h.tokens = h.note.content, 0
        used, dropped, counter = 0, 0, active_counter_name()

    # --- REINFORCE: ONLY on returned hits, in ONE transaction (5.9) ---
    if hits:
        store.reinforce([h.note.id for h in hits], now=utcnow())       # NOT on dropped/candidates

    return SearchResult(hits=hits, used=used, budget=token_budget,
                        dropped=dropped, counter=counter)
```

**Invariant — reinforce fires only on emitted hits.** Candidates fetched in fan-out but not returned (cut by `k` or by budget `dropped`) are NOT reinforced. This is what makes "being surfaced" the reinforcement signal (ux §57). Budget-dropped notes are likewise not reinforced (they were ranked but never shown). `reinforce()` is best-effort: wrap in try/except, log, never fail the search on a reinforce error (read path stays fast — SPEC §5 "읽기는 빠르게 유지").

---

### 5.2 FILTER (Store-level WHERE, applied inside knn/bm25)

Every channel query carries the same predicate. `Store.knn`/`Store.bm25` signatures gain `as_of`:

```python
def knn(self, emb: bytes|np.ndarray, k: int, scope: Scope,
        statuses: tuple[str,...], as_of: datetime|None) -> list[tuple[str,float]]: ...
def bm25(self, query: str, k: int, scope: Scope,
         statuses: tuple[str,...], as_of: datetime|None) -> list[tuple[str,float]]: ...
```

WHERE clause (notes), parameterized:
```sql
WHERE user_id = :user_id
  AND (:agent_id   IS NULL OR agent_id   = :agent_id)
  AND (:session_id IS NULL OR session_id = :session_id)
  AND status IN (:statuses)
  AND quarantined = 0                                   -- provenance invariant (D-T4)
  -- temporal (only when as_of given); default = currently-valid + not-expired:
  AND ( :as_of IS NULL
        AND (invalid_at IS NULL OR invalid_at > :now)   -- default: still valid
        AND (expired_at IS NULL OR expired_at > :now)
     OR :as_of IS NOT NULL
        AND valid_at  <= :as_of
        AND (invalid_at IS NULL OR invalid_at > :as_of)
        AND (expired_at IS NULL OR expired_at > :as_of) )
```

`quarantined` is a new column (see audit cross-fix below). Default search excludes it; Triage reads `quarantined = 1`.

> **Cross-fix (audit: quarantine state).** Add to `notes` DDL: `quarantined INTEGER NOT NULL DEFAULT 0`, `held_for_human INTEGER NOT NULL DEFAULT 0`, `triage_reason TEXT`. `Status` stays the closed set `active|archived|deleted`; quarantine is a flag orthogonal to status (a note can be `active` + `quarantined=1` = persisted but provenance-less/low-conf, excluded from search, shown only in Triage). Store gains `held_for_triage(scope) -> list[Note]` reading `held_for_human=1 OR quarantined=1`.

---

### 5.3 SEMANTIC channel (KNN)

- Embed `query` with the **configured embedder** (default `HashEmbedder`). Embedding dim = `embedder.dim` (NOT hardcoded 1536 — see cross-fix). Returns cosine-desc list.
- Default storage: `note_vec(note_id TEXT PRIMARY KEY, embedding BLOB, dim INTEGER, embedder_id TEXT)`, numpy brute-force KNN:
  ```python
  def knn(self, emb, k, scope, statuses, as_of):
      ids, mat = self._load_matrix(scope, statuses, as_of, embedder_id=self.embedder.id)  # (N,dim) float32
      if mat.shape[0] == 0: return []
      q = l2norm(np.asarray(emb, np.float32))
      sims = mat @ q                          # mat pre-normalized at write time
      idx = np.argpartition(-sims, min(k, len(sims)-1))[:k]
      idx = idx[np.argsort(-sims[idx])]
      return [(ids[i], float(sims[i])) for i in idx]
  ```
  `_load_matrix` filters by `embedder_id = self.embedder.id` so vectors from a different embedder (post-upgrade) are never KNN-compared (cross-fix below). With `[vec]` extra, replace with `vec0` MATCH; same `dim`/filter contract.

> **Cross-fix (audit: embedding dim & cross-tier).** (1) Embedding dim is a property of the embedder, stored per-row (`note_vec.dim`, `note_vec.embedder_id`) and in DB meta (`meta(key,value)` row `embedder_id`, `embedder_dim`). (2) DDL must NOT hardcode `FLOAT[1536]`; default numpy path uses `embedding BLOB`. The `[vec]` `vec0` table is created lazily with `FLOAT[<embedder.dim>]` at first init, recorded in meta. (3) On embedder change, KNN filters to current `embedder_id` → stale-embedder vectors are invisible to search until a background `reembed` job (kind=`reembed`, jobs table) re-encodes them; mixed-embedder KNN is structurally impossible (filter blocks it). Block embedder change if a reembed job is pending and warn in `doctor`.

---

### 5.4 BM25 channel (FTS5) + adaptive sigmoid normalization

- `note_fts MATCH :query` ordered by `bm25(note_fts)` ascending (FTS5 returns negative; lower = better → negate to a positive raw score `r = -bm25_raw`).
- Normalize raw → [0,1] with query-length-adaptive sigmoid (port of mem0 `scoring.py:16-54`, verbatim params):

```python
# cold_frame/read/retrieve.py
def get_bm25_params(query: str) -> tuple[float, float]:
    n = max(1, len(query.split()))           # whitespace tokens (no lemmatizer dep in core)
    if n <= 3:  return 5.0, 0.7
    if n <= 6:  return 7.0, 0.6
    if n <= 9:  return 9.0, 0.5
    if n <= 15: return 10.0, 0.5
    return 12.0, 0.5

def normalize_bm25_scores(query, raw: list[tuple[str,float]]) -> dict[str,float]:
    mid, steep = get_bm25_params(query)
    return {nid: 1.0/(1.0+math.exp(-steep*((-r) - mid))) for nid, r in raw}
    #                                    ^^^ -r converts FTS5 negative to positive magnitude
```

> Note: mem0 lemmatizes before counting terms; core has no lemmatizer dep (D4 keyless/offline). Use whitespace token count. `[openai]`/`[local-llm]` extras MAY override `get_bm25_params` query length via a lemmatized count, but the core default is whitespace.

---

### 5.5 EDGE channel (1-hop boost + promiscuity downweight + temporal edges)

```python
def edge_channel(sem_list, bm25_raw, scope, statuses, as_of):
    seeds = {nid for nid,_ in sem_list[:8]} | {nid for nid,_ in bm25_raw[:8]}  # top seeds only
    neigh_scores: dict[str,float] = defaultdict(float)
    edge_weight_map: dict[str,float] = {}
    for sid in seeds:
        # neighbors() applies the SAME temporal filter to EDGES (audit fix):
        for (dst, relation, weight, deg) in store.neighbors(
                sid, scope, statuses, as_of, relations=BOOST_RELATIONS):
            promiscuity = 1.0 / (1.0 + 0.001 * (deg - 1) ** 2)   # SPEC §5 step2
            contrib = weight * promiscuity
            neigh_scores[dst] += contrib
            edge_weight_map[dst] = max(edge_weight_map.get(dst, 0.0), contrib)
    ranked = sorted(neigh_scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked, edge_weight_map

BOOST_RELATIONS = ("relates_to", "supersedes", "derived_from")  # mentions/caused_by excluded from boost
```

> **Temporal edges (audit fix).** `Store.neighbors` filters edges by validity exactly like notes:
> ```sql
> WHERE src_id = :sid AND relation IN (:relations)
>   AND ( :as_of IS NULL AND (invalid_at IS NULL OR invalid_at > :now)
>      OR :as_of IS NOT NULL AND valid_at <= :as_of AND (invalid_at IS NULL OR invalid_at > :as_of) )
> ```
> `deg` (promiscuity degree) = count of edges of the **dst** node valid under the same `as_of` (`SELECT count(*) FROM edges WHERE (src_id=:dst OR dst_id=:dst) AND <temporal>`). So at an `as_of` time-travel point, both the edge set and the hub-degree reflect that moment — a node that became promiscuous later is not downweighted in the past view. Signature: `neighbors(node_id, scope, statuses, as_of, relations) -> list[(dst_id, relation, weight, degree)]`.

---

### 5.6 FUSE — RRF (k_const = 60, multi-channel, edge-weighted)

Port of graphiti `rrf` (`search_utils.py:1780`) but: (a) `k_const = 60` (SPEC §5 step3, vs graphiti default 1), (b) the **edge channel's per-item RRF contribution is scaled by its promiscuity-downweighted weight** so a hub neighbor doesn't get full positional credit, (c) returns `rank_in` for recall-receipt.

```python
def rrf_fuse(channels: dict[str, list[str]], k_const: int,
             edge_weight_fn) -> list[tuple[str, float, dict[str,int]]]:
    scores: dict[str, float] = defaultdict(float)
    rank_in: dict[str, dict[str,int]] = defaultdict(dict)
    for ch_name, id_list in channels.items():
        for i, nid in enumerate(id_list):
            base = 1.0 / (i + k_const)          # 0-based rank, k_const=60
            if ch_name == "edge":
                base *= edge_weight_fn(nid)     # downweight promiscuous-hub contributions
            scores[nid] += base
            rank_in[nid][ch_name] = i
    out = [(nid, sc, dict(rank_in[nid])) for nid, sc in scores.items()]
    out.sort(key=lambda t: (t[1], -_tiebreak_recency_rank(t[0])), reverse=True)  # tie: newer first
    return out
```

- **No global divisor / no per-channel weight tuning** beyond the edge weight (avoids the "global-divisor footgun" SPEC §5 step3 warns about; RRF is parameter-light by design).
- **Tie-break (deterministic):** equal RRF → more-recent `created_at` wins (`_tiebreak_recency_rank` = dense rank of created_at desc; stable). Guarantees reproducible ordering for eval.

---

### 5.7 RERANK (optional, default off) + meta boost

```python
async def rerank_hits(query, hits, scope):
    if rerank_backend == "bge":      # [local-llm]
        scores = bge_cross_encoder(query, [h.packed_or_content for h in hits])  # ∈ R
    elif rerank_backend == "llm":    # [openai] API path: boolean-relevant + logprob
        scores = await llm_boolean_logprob(query, [h.note.content for h in hits])# ∈ [0,1]
    else:
        return apply_meta_boost(hits, query)        # no backend -> meta boost only
    for h, s in zip(hits, scores):
        h.signals.rerank = float(s)
        h.score = float(s)                          # rerank REPLACES rrf as sort key
    return apply_meta_boost(hits, query)            # meta boost layered on top

def apply_meta_boost(hits, query):
    now = utcnow()
    for h in hits:
        w = 0.0
        # recency: half-life 30d on last_accessed (cheap, read-safe)
        dt_days = (now - (h.note.last_accessed or h.note.created_at)).total_seconds()/86400
        w += 0.10 * math.exp(-dt_days / 30.0)
        # scope precision: exact session/agent match > user-only
        if h.note.scope.session_id and h.note.scope.session_id == query_scope.session_id:
            w += 0.05
        h.signals.meta_boost = 1.0 + w
        h.score = min(1.0 * h.score * (1.0 + w), h.score * 1.15)   # clamp boost ≤ +15%
    return hits
```

- Meta boost is a **multiplier on the existing score** (rrf or rerank), clamped to ≤ +15% so it nudges, never dominates (SPEC §5 step4 `score*=(1+w) clamp`).
- RERANK is the ONLY place the LLM/cross-encoder touches the read path, and it's off by default → default read path is fully deterministic for eval (SPEC §10 철칙).

---

### 5.8 ★ TOKEN-BUDGET PACKER (the differentiator)

```python
# cold_frame/read/budget.py
def pack_budget(hits: list[Hit], budget: int) -> tuple[list[Hit], int, int, str]:
    counter = get_token_counter()                       # 5.10
    out: list[Hit] = []
    used = 0
    dropped = 0
    for h in hits:                                       # hits already sorted desc
        full = counter.count(h.note.content)
        remaining = budget - used
        if remaining <= 0:
            dropped += 1
            continue
        if full <= remaining:                            # whole note fits
            h.packed_content, h.tokens, h.truncated = h.note.content, full, False
            used += full
            out.append(h)
            continue
        # note exceeds remaining -> try truncation
        cut = truncate_to_tokens(h.note.content, remaining, counter)
        cut_tokens = counter.count(cut)
        if cut_tokens >= MIN_USEFUL_TOKENS and is_self_contained(cut):
            h.packed_content, h.tokens, h.truncated = cut, cut_tokens, True
            used += cut_tokens
            out.append(h)
            # budget now ~full; loop continues, subsequent notes will be dropped
        else:
            dropped += 1                                 # truncated stub not useful -> skip
    return out, used, dropped, counter.name

MIN_USEFUL_TOKENS = 12      # below this a truncated fact is a meaningless stub
```

**Algorithm = greedy pack in final-rank order under a hard token cap.** Rationale: ranking already encodes importance; greedy preserves the "top results always included" eval guarantee (SPEC §10 token-budget case) without a knapsack's reordering (which would violate "상위 strength 포함").

**Truncation policy** (`truncate_to_tokens`): atomic facts are 15–80 chars (SPEC §2) so they almost never need truncation — truncation matters mainly for `--raw` verbatim notes and long episodic content.
```python
def truncate_to_tokens(text, max_tokens, counter):
    # 1) sentence-boundary greedy: keep whole sentences while under budget
    sents = split_sentences(text)            # regex on [.!?]+ ; no nltk dep
    acc, toks = [], 0
    for s in sents:
        t = counter.count(s)
        if toks + t > max_tokens: break
        acc.append(s); toks += t
    if acc:
        return " ".join(acc) + (" …" if len(acc) < len(sents) else "")
    # 2) no whole sentence fits -> hard char cut at the token boundary + ellipsis
    return counter.truncate(text, max_tokens - 1) + "…"   # reserve 1 token for the ellipsis
```

**Edge cases (all specified):**
1. **Single note exceeds the entire budget** (`budget < full` for the very first hit): attempt truncation to `budget`. If `is_self_contained(cut)` and `cut_tokens ≥ MIN_USEFUL_TOKENS`, emit it truncated (better one partial top fact than empty). Else emit it truncated anyway IF it is the only hit considered (`out == []` and this is the last viable candidate) so the result is never empty when something was asked for; otherwise `dropped++`. Concretely: if after the loop `out == []` and `hits` non-empty, force-emit `hits[0]` truncated to `budget` with `truncated=True` (guarantee: non-empty result when budget>0 and ≥1 candidate).
2. **Ties in rank:** resolved upstream in `rrf_fuse` (recency tie-break) → packer sees a total order, no ambiguity.
3. **`budget <= 0`:** return `([], 0, len(hits), counter.name)` — empty, all dropped.
4. **Exact fit:** `full == remaining` → included whole (uses `<=`).
5. **Truncated note then a later tiny note that fits remaining:** allowed — after a truncation `remaining` is usually ~0 so later notes drop, but if truncation undershot (sentence boundary left slack), a subsequent small note may still fit. This is correct (maximizes budget use) and deterministic.
6. **`is_self_contained`** = cut still contains the subject (heuristic: first sentence retained, no dangling pronoun-only fragment). Cheap: `len(cut.split()) >= 3 and cut[0].isupper()`-style check; conservative — when unsure, treat as not-useful and drop rather than emit a confusing fragment.

`SearchResult.used <= budget` is a hard post-condition (assert in tests). `dropped` is surfaced in CLI/MCP so the caller knows facts were withheld for budget.

---

### 5.9 REINFORCE side-effect (single transaction, returned hits only)

```python
# cold_frame/store/sqlite.py
def reinforce(self, note_ids: list[str], now: datetime) -> None:
    if not note_ids: return
    with self.tx() as cx:                       # ONE transaction
        cx.executemany(
            "UPDATE notes SET access_count = access_count + 1, "
            "                 last_accessed = :now, "
            "                 decay_S = MIN(decay_S + :inc, :cap) "
            " WHERE id = :id",
            [{"now": iso(now), "inc": REINFORCE_DECAY_INC, "cap": DECAY_S_CAP, "id": i}
             for i in note_ids])
        # access_log append (one row per reinforced note per search) — see retention below
        cx.executemany(
            "INSERT INTO access_log(note_id, ts) VALUES (:id, :ts)",
            [{"id": i, "ts": iso(now)} for i in note_ids])

REINFORCE_DECAY_INC = 0.5     # decay_S is a stability constant; recall raises it (slows future decay)
DECAY_S_CAP = 365.0           # cap stability so a hot fact can't become un-decayable
```

> **`decay_S++` clarification.** SPEC §5 writes `decay_S++` loosely. Canonical: `decay_S = min(decay_S + 0.5, 365)`. `decay_S` is the FSRS-style *stability* (days-scale) used by retrievability `e^(−Δt/decay_S)`; each recall increases stability (slower future forgetting), capped so nothing becomes immortal without a pin.

> **Cross-fix (audit: `access_log` is in SPEC §2 but absent from DDL, no write path, unbounded-growth risk vs R5).** Add to design.md §2.3:
> ```sql
> CREATE TABLE access_log (note_id TEXT NOT NULL, ts TEXT NOT NULL);
> CREATE INDEX idx_access_log_note ON access_log(note_id, ts);
> ```
> **Write path:** exactly one row per (note, search-that-returned-it), written in the `reinforce()` transaction above — NOT per channel, NOT per candidate. So N returned hits → N rows per search.
> **Retention/compaction (satisfies R5 anti-unbounded-growth):** a `consolidate()` sub-job `compact_access_log` (kind=`compact_access_log`, debounced, runs with consolidation) enforces **cap = 50 most-recent rows per note**; older rows are downsampled to **≤1 row/day** (keep the day's last ts) then to **≤1 row/week beyond 90 days**. This preserves the forgetting-curve sparkline shape (re-spike timestamps) while bounding rows to ~O(50 + weeks_alive) per note. Implementation:
> ```sql
> -- keep newest 50 raw; collapse older same-day to last; older-than-90d to weekly
> DELETE FROM access_log WHERE rowid IN (
>   SELECT rowid FROM (
>     SELECT rowid, row_number() OVER (PARTITION BY note_id ORDER BY ts DESC) rn
>     FROM access_log) WHERE rn > 50);
> -- (day/week downsample done in Python over the survivors per note)
> ```
> If `access_log` is dropped/absent, the sparkline degrades to the current-strength meter (ux §8.10, §1.2) — search and reinforce of the scalar `access_count`/`last_accessed` still work (table is additive-only).

---

### 5.10 OFFLINE token counting (no heavy dep)

```python
# cold_frame/llm/tokens.py
class TokenCounter(Protocol):
    name: str
    def count(self, text: str) -> int: ...
    def truncate(self, text: str, max_tokens: int) -> str: ...

class HeuristicCounter:                          # DEFAULT — zero deps, offline (D4)
    name = "heuristic-chars4"
    def count(self, text: str) -> int:
        # chars/4 floor=1 for non-empty; matches GPT-family avg English ratio.
        # Add a small word-count blend to avoid under-counting CJK/code:
        c = len(text); w = len(text.split())
        return max(1, round(0.75 * (c / 4) + 0.25 * w)) if text else 0
    def truncate(self, text, max_tokens):
        return text[: max_tokens * 4]            # inverse of chars/4; caller adds ellipsis

class TiktokenCounter:                           # OPTIONAL via [openai] (or [tokenizers] extra)
    name = "tiktoken:cl100k_base"
    def __init__(self): import tiktoken; self.enc = tiktoken.get_encoding("cl100k_base")
    def count(self, text): return len(self.enc.encode(text))
    def truncate(self, text, max_tokens):
        return self.enc.decode(self.enc.encode(text)[:max_tokens])

def get_token_counter() -> TokenCounter:
    if config.token_counter == "tiktoken":
        try: return TiktokenCounter()
        except ImportError: pass                 # graceful fallback, never crash offline
    return HeuristicCounter()
```

- **Default = `HeuristicCounter`** (chars/4 with a word blend) so budget works fully offline, keyless, dep-free (D4/R11). The blend term keeps CJK and code from being wildly under-counted (pure chars/4 under-estimates CJK tokens ~2-4×; the 0.25·words term partially corrects).
- **Optional `tiktoken`** via extra for exact GPT-family counts; selected by `config token_counter = tiktoken`. `cl100k_base` is the safe default encoding.
- The active counter name is returned in `SearchResult.counter` so the caller/eval knows which was used (heuristic budgets are approximate — document that `used` may be ±15% of a true tokenizer when heuristic).

---

### Cross-doc reconciliation: strength `S` & archive score (audit: §6/§8.5 vs ux §8.2/§4.3)

The read path computes `Signals.strength` for every hit, so the formula must be pinned. **Canonical (used everywhere — read display, list glyph, sparkline):**

```
S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access_count)/log1p(20))
retrievability = e^(−Δt_last_accessed / decay_S)         # ∈ [0,1]
```
This is SPEC §6/§8.5 verbatim and is the ONE display-strength formula. ux §4.3's `.42/.30/.18` Fact-Detail weights are **superseded** by this (mark ux §4.3 as "illustrative; canonical = §8.5"). The §6-step-1 decay `score` (`w_r·e^… + w_i·importance + w_rel·relevance`) is a **separate consolidation/archive score**, NOT the display strength — rename it `archive_score` in §6 to end the conflation.

**Bands (4-band, reconciling SPEC §6's 3 bands with ux §8.2's 4):** adopt ux §8.2 as canonical, where the 4th is a *sub-band* of fading (not a new top-level band):
```
S ≥ 0.66            🌳 evergreen   (SPEC: evergreen)
0.33 ≤ S < 0.66     🌿 budding
0.10 ≤ S < 0.33     🌱 fading      (SPEC's "fading")
S < 0.10            ·  ember       (fading, dimmed, "~Nd → archive")   <- sub-band
+ at-risk(○) overlay when confidence < 0.4 OR last_accessed > 60d  (band-independent)
```
Update SPEC §6 to list the 0.10 ember cut so the glyph band matches ux §8.2 exactly.

**Archive must not disagree with the glyph (audit: a fact showing 🌳 while archive-imminent).** Pin the rule: **a note is archive-eligible ONLY when its display `S < ARCHIVE_FLOOR = 0.10`** (i.e. inside the ember sub-band) AND `consolidate()`'s capacity-cap/`archive_score` selects it. So archive ⊆ ember. The capacity-cap path (SPEC §6 step4) still ranks ember-band notes by `archive_score` to pick which to demote, but never archives anything ≥ 0.10. This guarantees the §8.10 render contract: nothing labeled evergreen/budding can be silently archived.

**Capacity-cap concrete numbers (audit: §6 'episodic 활성 N' has no value).** Defaults (config-overridable): `cap = {"episodic": 2000, "semantic": 5000, "procedural": 500}` active notes per type per scope. On overflow, demote lowest `archive_score` first, restricted to the ember band; pinned and pin-adjacent notes are exempt (§6 Triage (d)).
