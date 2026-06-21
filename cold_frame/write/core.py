"""WriteCore — the single persist path (I15, D8).

Leaf stub. Every entry runs the SAME pipeline: ADMISSION (CLASSIFY → REDACT →
CONFIDENCE-GATE → CONSENT) → DEDUP → CONFLICT → PERSIST, in ONE Store transaction (I3).
Bodies raise ``NotImplementedError``; P1+ fill them in without changing signatures.

- ``commit`` is used by ``add()`` and ``create_fact``.
- ``commit_supersede`` is used by ``correct_memory``, ``update_fact``, ``supersede`` —
  keyed by explicit id (NOT similarity search), the same commit as the conflict path.
"""

from __future__ import annotations

from cold_frame.llm.base import LLM, Clock, Embedder
from cold_frame.models import AddResult, Note, Scope, Source
from cold_frame.store.base import Store


class WriteCore:
    """The lone admission+persist pipeline shared by all write entrypoints (I15)."""

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

    def commit(
        self,
        candidates: list[Note],
        *,
        scope: Scope,
        source: Source | None = None,
    ) -> AddResult:
        """ADMISSION → DEDUP → CONFLICT → PERSIST for new candidate facts (SPEC §4)."""
        raise NotImplementedError

    def commit_supersede(
        self,
        old_id: str,
        new: Note,
        *,
        reason: str,
    ) -> Note:
        """Explicit-id supersede (correct/update_fact/supersede): old→archived +
        invalid_at=now + ``supersedes`` edge new→old + note_history, ONE txn (I3)."""
        raise NotImplementedError
