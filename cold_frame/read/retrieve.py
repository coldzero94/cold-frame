"""RetrievePipeline — search fan-out → RRF → rerank → token-budget pack (SPEC §5).

Leaf stub. ``search`` body raises ``NotImplementedError``; P1 (hybrid+RRF) and P3
(packer/rerank/meta boost) fill it in without changing the signature.
"""

from __future__ import annotations

from datetime import datetime

from cold_frame.llm.base import LLM, Clock, Embedder
from cold_frame.models import Scope, SearchResult
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
        """Default FILTER: ``status='active' AND NOT quarantined`` (G2). [] never raises."""
        raise NotImplementedError
