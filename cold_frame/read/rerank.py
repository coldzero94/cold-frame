"""Re-scoring steps (read-and-budget §5.7).

``apply_meta_boost`` — a small recency/scope multiplier on the fused score, clamped to <=+15% so it
nudges but never dominates RRF (the default read path stays fully deterministic for eval).
``llm_rerank`` — the OPT-IN relevance rerank: an LLM scores the top candidates against the query and
the result is re-sorted by that score (``Signals.rerank``). Off by default; runs only when the
caller passes ``rerank=True`` AND an LLM is configured.
"""

from __future__ import annotations

import math
from datetime import datetime

from cold_frame.llm.base import LLM, TaskTag
from cold_frame.models import Scope, SearchHit
from cold_frame.prompts.rerank import RERANK_SYSTEM, RerankOutput, build_rerank_user

_RECENCY_HALF_LIFE_DAYS = 30.0
_RECENCY_WEIGHT = 0.10
_SCOPE_MATCH_WEIGHT = 0.05
_BOOST_CLAMP = 0.15  # meta boost never lifts a score by more than +15%
# only the strongest fused candidates are sent to the LLM reranker (bounded cost)
_RERANK_TOP_K = 20


def apply_meta_boost(hits: list[SearchHit], *, now: datetime, scope: Scope) -> list[SearchHit]:
    """Re-score by recency + scope precision (clamped) and re-sort, in place."""
    for hit in hits:
        ref = hit.note.last_accessed or hit.note.created_at
        dt_days = max(0.0, (now - ref).total_seconds() / 86400.0)
        weight = _RECENCY_WEIGHT * math.exp(-dt_days / _RECENCY_HALF_LIFE_DAYS)
        if hit.note.scope.session_id is not None and hit.note.scope.session_id == scope.session_id:
            weight += _SCOPE_MATCH_WEIGHT
        hit.score = min(hit.score * (1.0 + weight), hit.score * (1.0 + _BOOST_CLAMP))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


def llm_rerank(query: str, hits: list[SearchHit], llm: LLM) -> list[SearchHit]:
    """OPT-IN: score the top ``_RERANK_TOP_K`` candidates by LLM relevance to ``query`` and re-sort
    by it (``Signals.rerank``). The LLM only proposes a score per stable id (I11); code disposes —
    a candidate the LLM omits keeps ``rerank=None`` and sinks below scored ones (ties → fused).
    Any LLM/parse failure leaves ``hits`` untouched (the fused order stands — relevance is additive,
    not a gate, so a rerank failure must NOT drop results).
    """
    if not hits:
        return hits
    head = hits[:_RERANK_TOP_K]
    res = llm.complete(
        task=TaskTag.RERANK_JUDGE,
        system=RERANK_SYSTEM,
        user=build_rerank_user(query, [(h.note.id, h.note.content) for h in head]),
        schema=RerankOutput,
    )
    if not isinstance(res.parsed, RerankOutput):
        return hits  # unparseable → keep the fused order (rerank never drops results)
    scored = {s.id: s.relevance for s in res.parsed.scores}
    for hit in head:
        hit.signals.rerank = scored.get(hit.note.id)
    # scored candidates first (by relevance desc), then unscored — each group keeps fused order.
    head.sort(key=lambda h: (h.signals.rerank is not None, h.signals.rerank or 0.0), reverse=True)
    return head + hits[_RERANK_TOP_K:]
