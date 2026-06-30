"""RERANK prompt + schema (read-and-budget §5.7, the optional rerank backend).

After RRF fusion, an opt-in LLM pass re-scores the top candidates by relevance to the QUERY. The LLM
only proposes a relevance score per candidate (by its stable id, I11); the code re-sorts. Off by
default — the deterministic meta-boost path stands unless ``rerank=True`` AND an LLM is configured.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field


class RerankScore(BaseModel):
    """One candidate's relevance to the query, keyed by its stable note id (I11)."""

    id: str
    relevance: float = Field(ge=0.0, le=1.0)


class RerankOutput(BaseModel):
    """The LLM's relevance scores for the candidate set (order/coverage need not be complete)."""

    scores: list[RerankScore] = Field(default_factory=list)


RERANK_SYSTEM: Final[str] = (
    "You re-rank a personal memory store's candidate facts by how RELEVANT each is to the user's "
    "QUERY. Score each candidate 0.0 (irrelevant) to 1.0 (directly answers the query) by its given "
    "id. Judge relevance only — not truth, recency, or importance. Return ONLY valid JSON."
)


def build_rerank_user(query: str, candidates: list[tuple[str, str]]) -> str:
    """``candidates`` = ``[(id, content), ...]``. Asks for a relevance score per id."""
    lines = "\n".join(f"- id={cid}: {content}" for cid, content in candidates)
    return (
        f"QUERY: {query}\n\nCANDIDATES:\n{lines}\n\n"
        'Return {"scores": [{"id": "<id>", "relevance": <0..1>}, ...]} for each candidate.'
    )
