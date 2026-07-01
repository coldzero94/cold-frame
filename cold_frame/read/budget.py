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

    Returns ``(kept hits, used tokens, truncated)``. ``used`` is the ACTUAL token count of the
    emitted facts (so a caller can size its prompt honestly). It exceeds ``budget`` ONLY in the
    single non-empty bend: when even the top-ranked fact alone is over budget it is still emitted
    (better one fact than none), and ``used`` reports its REAL (over-budget) size — not a capped
    lie — with ``truncated=True`` flagging that the cap was blown to keep a result.
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
            used = tokens  # the REAL count — honest even when it exceeds budget (the bend below)
            if tokens > budget:  # oversized top fact → emitted whole; flag the blown cap
                truncated = True
            continue
        if used + tokens <= budget:  # whole fact fits → include
            kept.append(hit)
            used += tokens
        else:  # a retrievable fact was withheld to fit the cap → flag it (SPEC's `dropped`)
            truncated = True
    return kept, used, truncated
