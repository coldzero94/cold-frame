"""Meta boost + optional rerank hook (read-and-budget §5.7).

Meta boost (default, deterministic): a small recency/scope multiplier on the fused
score, clamped to <=+15% so it nudges but never dominates RRF — the default read path
stays fully deterministic for eval. Cross-encoder/LLM rerank backends are extras
(``[local-llm]``/``[openai]``), off by default; without a backend, rerank falls back to
meta boost (so ``rerank=True`` is safe offline).
"""

from __future__ import annotations

import math
from datetime import datetime

from cold_frame.models import Scope, SearchHit

_RECENCY_HALF_LIFE_DAYS = 30.0
_RECENCY_WEIGHT = 0.10
_SCOPE_MATCH_WEIGHT = 0.05
_BOOST_CLAMP = 0.15  # meta boost never lifts a score by more than +15%


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
