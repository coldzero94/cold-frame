"""Reciprocal Rank Fusion (SPEC §5 step 3 / read-and-budget §5.6).

Parameter-light fusion of per-channel rank lists: ``score(nid) = Σ 1/(rank + k_const)``
with ``k_const=60`` and NO global divisor (the documented footgun). v1 fuses TWO channels:
semantic + bm25 (unweighted). The ``weight_fn`` param + the ``edge`` channel branch are a RESERVED
seam for the 1-hop graph edge recall channel cut from v1 (D27) — unused in production ``retrieve``,
kept test-pinned for a future re-add. Ties break deterministically by recency (more-recent first),
then id (eval-stable).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable


def rrf_fuse(
    channels: dict[str, list[str]],
    k_const: int,
    weight_fn: Callable[[str], float] | None = None,
    *,
    recency_rank: Callable[[str], int] | None = None,
) -> list[tuple[str, float]]:
    """Fuse channel rank-lists into one ordered ``[(note_id, rrf_score)]`` (desc)."""
    scores: dict[str, float] = defaultdict(float)
    for channel, id_list in channels.items():
        for rank, nid in enumerate(id_list):  # 0-based rank
            base = 1.0 / (rank + k_const)
            if channel == "edge" and weight_fn is not None:
                base *= weight_fn(nid)  # down-weight promiscuous-hub contributions
            scores[nid] += base

    def sort_key(item: tuple[str, float]) -> tuple[float, int, str]:
        nid, score = item
        rr = recency_rank(nid) if recency_rank is not None else 0
        return (-score, rr, nid)  # score desc; then more-recent (lower rank); then id

    return sorted(scores.items(), key=sort_key)
