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
) -> tuple[list[SearchHit], int, bool]:
    """Pack whole facts in rank order under ``budget``.

    Returns ``(kept hits, used tokens, truncated)``. ``used`` NEVER exceeds ``budget`` (a
    hard cap). The non-empty guarantee is the single bend: when even the top-ranked fact
    alone exceeds the budget it is still emitted (better one fact than none), but ``used``
    is reported as the full budget and ``truncated=True`` flags that the cap was hit.
    """
    if budget <= 0 or not hits:
        return [], 0, False
    kept: list[SearchHit] = []
    used = 0
    truncated = False
    for hit in hits:
        tokens = counter.count(hit.note.content)
        if not kept:  # non-empty guarantee: the top-ranked hit is always emitted
            kept.append(hit)
            if tokens > budget:  # oversized top fact → emit whole, but report a capped used
                used = budget
                truncated = True
            else:
                used = tokens
            continue
        if used + tokens <= budget:  # whole fact fits → include
            kept.append(hit)
            used += tokens
        else:  # a retrievable fact was withheld to fit the cap → flag it (SPEC's `dropped`)
            truncated = True
    return kept, used, truncated
