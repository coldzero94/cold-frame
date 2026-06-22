"""Token-budget packer (read-and-budget §5.8) — the differentiator.

Greedy pack in final-rank order under a hard token cap: ranking already encodes
importance, so the top results are always included (no knapsack reordering that would
violate "top-strength included"). P3 packs WHOLE atomic facts (15-80 chars ≈ a few
tokens each); per-hit partial truncation is a later refinement. A non-empty result is
guaranteed whenever budget>0 and there is at least one candidate.
"""

from __future__ import annotations

from cold_frame.llm.tokens import TokenCounter
from cold_frame.models import SearchHit


def pack_budget(
    hits: list[SearchHit], budget: int, counter: TokenCounter
) -> tuple[list[SearchHit], int]:
    """Return ``(kept hits, used tokens)`` packing whole facts in rank order under ``budget``."""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits:
        tokens = counter.count(hit.note.content)
        if not kept:  # non-empty guarantee: the top-ranked hit is always emitted
            kept.append(hit)
            used += tokens
            continue
        if used + tokens <= budget:  # whole fact fits → include; else skip + keep trying
            kept.append(hit)
            used += tokens
    return kept, used
