"""RetrievePipeline — search fan-out → RRF → rerank → token-budget pack (SPEC §5).

Leaf stub. ``search`` body raises ``NotImplementedError``; P1 (hybrid+RRF) and P3
(packer/rerank/meta boost) fill it in without changing the signature.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime

from cold_frame.constants import FANOUT, FANOUT_MAX, FANOUT_MIN, RRF_K
from cold_frame.exceptions import StoreError
from cold_frame.llm.base import LLM, Clock, Embedder
from cold_frame.models import Scope, SearchHit, SearchResult, Signals, StatusLiteral
from cold_frame.read.fuse import rrf_fuse
from cold_frame.store.base import Store


class RetrievePipeline:
    """Hybrid retrieve → RRF fuse → optional rerank → budget pack → REINFORCE."""

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
        rerank: bool = False,
    ) -> SearchResult:
        """Hybrid retrieve → RRF fuse → top-k → REINFORCE (P1; rerank/budget are P3).

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
        for nid, rrf_score in fused[:k]:
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

        if hits:  # REINFORCE only emitted hits, best-effort (read path stays fast, SPEC §5)
            with suppress(StoreError):
                self._store.reinforce([h.note.id for h in hits], now=self._clock.now())

        return SearchResult(hits=hits, used_tokens=None, truncated=False)
