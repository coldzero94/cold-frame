"""RetrievePipeline — search fan-out → RRF fuse → meta-boost → token-budget pack (SPEC §5).

Implemented across P1 (hybrid retrieve + RRF) and P3 (deterministic meta boost + the packer). An
opt-in LLM relevance rerank (``search(rerank=True)``) re-scores the top candidates; off by default.
"""

from __future__ import annotations

from datetime import datetime

from cold_frame.constants import FANOUT, FANOUT_MAX, FANOUT_MIN, RRF_K
from cold_frame.exceptions import StoreError
from cold_frame.llm.base import LLM, Clock, Embedder
from cold_frame.llm.tokens import get_token_counter
from cold_frame.models import Scope, SearchHit, SearchResult, Signals, StatusLiteral
from cold_frame.observability import get_logger
from cold_frame.read.budget import pack_budget
from cold_frame.read.fuse import rrf_fuse
from cold_frame.read.rerank import apply_meta_boost, llm_rerank
from cold_frame.store.base import Store

_log = get_logger(__name__)


class RetrievePipeline:
    """Hybrid retrieve → RRF fuse → meta boost → budget pack → REINFORCE."""

    def __init__(
        self,
        store: Store,
        *,
        embedder: Embedder,
        llm: LLM | None,
        clock: Clock,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._clock = clock

    def search(
        self,
        query: str,
        *,
        scope: Scope,
        k: int = 10,
        token_budget: int | None = None,
        as_of: datetime | None = None,
        include_archived: bool = False,
        reinforce: bool = True,
        rerank: bool = False,
    ) -> SearchResult:
        """Hybrid retrieve → RRF fuse → meta-boost → (opt-in LLM rerank) → top-k → REINFORCE.

        Default FILTER = ``status='active' AND NOT quarantined`` (G2, enforced in the
        Store channels). Never raises on no match — returns an empty SearchResult.
        """
        # Default = active only (currently-valid). With as_of, bypass the status filter (C3):
        # the TRUE predicate (valid_at<=as_of<invalid_at, applied in the Store channels) decides
        # membership, so a since-archived note that WAS valid at as_of is surfaced.
        statuses: list[StatusLiteral] = (
            ["active", "archived"] if as_of is not None or include_archived else ["active"]
        )
        cand_k = max(FANOUT_MIN, min(FANOUT_MAX, k * FANOUT))  # over-fetch per channel

        query_emb = self._embedder.embed_one(query)
        sem = self._store.knn(query_emb, cand_k, scope=scope, statuses=statuses, as_of=as_of)
        bm = self._store.bm25(query, cand_k, scope=scope, statuses=statuses, as_of=as_of)
        sem_ids = [nid for nid, _ in sem]
        bm_ids = [nid for nid, _ in bm]

        cand_ids = list(dict.fromkeys(sem_ids + bm_ids))  # union, order-preserving
        if not cand_ids:
            return SearchResult(hits=[], used_tokens=None, truncated=False)

        note_map = {n.id: n for n in self._store.get_notes(cand_ids)}
        # The 1-hop graph edge channel was cut from the recall path (v1 scope): the graph is
        # sparsely populated (only supersedes/derived_from + rare manual relates_to) so it rarely
        # surfaced anything, and it twice caused cross-scope/quarantine leak bugs. Edge ROWS stay
        # (consolidation + history + supersede links); they just no longer expand search results.
        by_recency = sorted(note_map.values(), key=lambda n: n.created_at, reverse=True)
        recency = {n.id: i for i, n in enumerate(by_recency)}
        fused = rrf_fuse(
            {"semantic": sem_ids, "bm25": bm_ids},
            RRF_K,
            recency_rank=lambda nid: recency.get(nid, len(recency)),
        )

        sem_scores = dict(sem)
        bm_scores = dict(bm)
        hits: list[SearchHit] = []
        for nid, rrf_score in fused:  # build over ALL fused candidates (boost may reorder)
            note = note_map.get(nid)
            if note is None:
                continue
            hits.append(
                SearchHit(
                    note=note,
                    score=rrf_score,
                    signals=Signals(
                        rrf=rrf_score,
                        semantic=sem_scores.get(nid),
                        bm25=bm_scores.get(nid),
                        edge=None,
                        rerank=None,
                    ),
                )
            )

        # META BOOST (default; the optional LLM/BGE rerank backend is an extra). Clamped to
        # +15% so it nudges, never dominates RRF — read path stays deterministic for eval.
        apply_meta_boost(hits, now=self._clock.now(), scope=scope)
        # OPT-IN LLM rerank (an extra): re-score the top candidates by query relevance BEFORE the
        # top-k cut, so it can promote within the candidate set. A failure leaves the fused order.
        if rerank and self._llm is not None:
            hits = llm_rerank(query, hits, self._llm)
        hits = hits[:k]  # truncate to k AFTER boost/rerank (they may promote within the set)

        # BUDGET: pack whole facts under the cap BEFORE reinforce, so budget-dropped notes
        # are not reinforced (being *surfaced* is the reinforcement signal — §5.9).
        used_tokens: int | None = None
        truncated = False
        if token_budget is not None:
            hits, used_tokens, truncated = pack_budget(hits, token_budget, get_token_counter())

        # REINFORCE only emitted hits, best-effort (read path stays fast, SPEC §5). `reinforce` is
        # False for the local UI: surfacing memories in a viewer is NOT the agent re-accessing them,
        # and an ungated GET must not let a drive-by page bump decay/access (write-via-GET). Also
        # skipped on a historical (as_of) read: surfacing a past belief must not bump a
        # since-archived note's decay/access (it would falsify recency on a later revive).
        if hits and reinforce and as_of is None:
            try:
                self._store.reinforce([h.note.id for h in hits], now=self._clock.now())
            except (
                StoreError
            ) as exc:  # never fail the search on a reinforce error — but log it (I16)
                _log.warning(
                    "reinforce_failed", extra={"note_count": len(hits), "err": type(exc).__name__}
                )

        return SearchResult(hits=hits, used_tokens=used_tokens, truncated=truncated)
