"""Consolidator — decay reinforcement, archive-by-score, capacity cap, triage flag.

Leaf stub. ``consolidate`` body raises ``NotImplementedError``; P4 fills it in
(archive only when ``S<0.33 AND archive_score<0.20`` OR over cap; non-destructive,
convergent; pinned/high-importance never archived — I13) without changing signatures.
"""

from __future__ import annotations

from datetime import datetime

from cold_frame.llm.base import LLM, Clock
from cold_frame.models import ConsolidateResult, Scope
from cold_frame.store.base import Store


class Consolidator:
    """Forgetting-curve maintenance: reinforce → merge → archive → flag triage (SPEC §6)."""

    def __init__(self, store: Store, *, llm: LLM | None, clock: Clock) -> None:
        self._store = store
        self._llm = llm
        self._clock = clock

    def consolidate(self, *, scope: Scope, now: datetime | None = None) -> ConsolidateResult:
        raise NotImplementedError
