"""RetrievePipeline — search fan-out → RRF fuse → meta-boost → token-budget pack (SPEC §5).

Implemented across P1 (hybrid retrieve + RRF) and P3 (deterministic meta boost + the packer). A
cross-encoder/LLM rerank backend is a deferred extra and is NOT wired in v1.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from cold_frame.constants import (
    EDGE_PROMISCUITY_PENALTY,
    EDGE_SEED_K,
    FANOUT,
    FANOUT_MAX,
    FANOUT_MIN,
    RRF_K,
)
from cold_frame.exceptions import StoreError
from cold_frame.llm.base import LLM, Clock, Embedder
from cold_frame.llm.tokens import get_token_counter
from cold_frame.models import Note, Scope, SearchHit, SearchResult, Signals, StatusLiteral
from cold_frame.observability import get_logger
from cold_frame.read.budget import pack_budget
from cold_frame.read.fuse import rrf_fuse
from cold_frame.read.rerank import apply_meta_boost
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
    ) -> SearchResult:
        """Hybrid retrieve → RRF fuse → top-k → meta-boost → REINFORCE (P1; meta-boost/budget P3).

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
        # EDGE CHANNEL: expand the strongest candidates to their 1-hop graph neighbors so a
        # graph-connected fact surfaces even if it didn't match semantically/lexically. A neighbor
        # reached via a promiscuous hub is down-weighted; edge-reached notes are scope+status
        # filtered (NEVER a cross-scope leak) before they can be returned.
        edge_ids, edge_weight = self._edge_channel(cand_ids, scope, statuses, note_map, as_of)
        by_recency = sorted(note_map.values(), key=lambda n: n.created_at, reverse=True)
        recency = {n.id: i for i, n in enumerate(by_recency)}
        fused = rrf_fuse(
            {"semantic": sem_ids, "bm25": bm_ids, "edge": edge_ids},
            RRF_K,
            weight_fn=lambda nid: edge_weight.get(nid, 1.0),
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
                        edge=edge_weight.get(nid),
                        rerank=None,
                    ),
                )
            )

        # META BOOST (default; the optional LLM/BGE rerank backend is an extra). Clamped to
        # +15% so it nudges, never dominates RRF — read path stays deterministic for eval.
        apply_meta_boost(hits, now=self._clock.now(), scope=scope)
        hits = hits[:k]  # truncate to k AFTER boost (boost may promote within the candidate set)

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

    def _edge_channel(
        self,
        cand_ids: list[str],
        scope: Scope,
        statuses: list[StatusLiteral],
        note_map: dict[str, Note],
        as_of: datetime | None,
    ) -> tuple[list[str], dict[str, float]]:
        """1-hop graph expansion of the top candidates → (edge-reached ids, promiscuity weight).

        Mutates ``note_map`` to include the scope+status-filtered reached notes. NEVER yields a note
        outside ``scope.user_id`` (leak guard). Skipped on a historical (as_of) read — the edge
        channel is a current-recall enhancer, and graph validity at a past instant isn't modeled.
        """
        if as_of is not None:
            return [], {}
        seeds = cand_ids[:EDGE_SEED_K]
        edges = self._store.neighbors(seeds, relations=None)
        if not edges:
            return [], {}
        # promiscuity = RAW edge count touching a hub (intentionally unfiltered by scope/status: a
        # hub wired to many things is a weak signal regardless of which neighbors are surfaceable).
        degree: dict[str, int] = defaultdict(int)
        for e in edges:
            degree[e.src_id] += 1
            degree[e.dst_id] += 1
        seed_set = set(seeds)
        weight: dict[str, float] = {}
        order: list[str] = []
        for e in edges:
            for hub, other in ((e.src_id, e.dst_id), (e.dst_id, e.src_id)):
                if hub not in seed_set or other in seed_set or other in note_map:
                    continue  # only NEW notes reached FROM a seed
                w = 1.0 / (1.0 + EDGE_PROMISCUITY_PENALTY * (degree[hub] - 1) ** 2)
                if other not in weight:
                    order.append(other)
                weight[other] = max(weight.get(other, 0.0), w)
        if not order:
            return [], {}
        # deterministic (eval-stable) rank: strongest weight first, id as tiebreak; then cap the
        # fan-out so a high-degree hub can't pull an unbounded set into get_notes/the fuse.
        order.sort(key=lambda nid: (-weight[nid], nid))
        order = order[:FANOUT_MAX]
        # Apply the EXACT search guard (scope + status + quarantine + bi-temporal in-effect gate)
        # by reusing the Store's `_where_clauses` via get_notes_filtered — NOT a hand-rolled Python
        # filter (which twice drifted: missed quarantine, then the invalid_at gate). as_of is always
        # None here (the channel early-returns above for historical reads).
        for n in self._store.get_notes_filtered(order, scope=scope, statuses=statuses, as_of=as_of):
            note_map[n.id] = n
        edge_ids = [nid for nid in order if nid in note_map]
        return edge_ids, {nid: weight[nid] for nid in edge_ids}
