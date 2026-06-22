"""WriteCore — the single persist path (I15, D8).

Leaf stub. Every entry runs the SAME pipeline: ADMISSION (CLASSIFY → REDACT →
CONFIDENCE-GATE → CONSENT) → DEDUP → CONFLICT → PERSIST, in ONE Store transaction (I3).
Bodies raise ``NotImplementedError``; P1+ fill them in without changing signatures.

- ``commit`` is used by ``add()`` and ``create_fact``.
- ``commit_supersede`` is used by ``correct_memory``, ``update_fact``, ``supersede`` —
  keyed by explicit id (NOT similarity search), the same commit as the conflict path.
"""

from __future__ import annotations

import numpy as np

from cold_frame.constants import DEDUP_AUTO_MERGE, DEDUP_NEAR_DUP
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import AddResult, ConflictVerdict, Note, Scope, Source
from cold_frame.prompts.conflict import DEDUP_SYSTEM, build_dedup_user
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
        """ADMISSION → DEDUP → CONFLICT → PERSIST for new candidate facts (SPEC §4).

        ADMISSION is pass-through for P2 (no secret classifier yet). DEDUP collapses a
        candidate into an existing note (tiered: cosine≥0.93 auto-merge, [0.82,0.93)
        ambiguous → LLM, else distinct). CONFLICT/freshness wiring is P2-4. Quarantined/
        held candidates are persisted but routed to ``held``.
        """
        added: list[Note] = []
        held: list[Note] = []
        deduped: list[str] = []
        for cand in candidates:
            emb = self._embedder.embed_one(cand.content)
            dup_id = self._find_duplicate(cand, emb, scope)
            if dup_id is not None:
                deduped.append(dup_id)  # non-destructive: drop the dup, existing note stays
                continue
            self._store.add_note(cand, emb)
            if cand.held_for_human or cand.quarantined:
                held.append(cand)
            else:
                added.append(cand)
        return AddResult(added=added, superseded=[], deduped=deduped, blocked=[], held=held)

    def _find_duplicate(self, cand: Note, emb: np.ndarray, scope: Scope) -> str | None:
        """Tiered DEDUP (SPEC §4): nearest active note → cosine band decision.

        ≥0.93 (incl. exact, cosine 1.0) → auto-merge; [0.82,0.93) → LLM judge (or distinct
        when offline); <0.82 → not a duplicate. The LLM only proposes sameness (I1).
        """
        hits = self._store.knn(emb, 5, scope=scope, statuses=["active"])
        if not hits:
            return None
        top_id, top_cos = hits[0]
        if top_cos >= DEDUP_AUTO_MERGE:
            return top_id
        if top_cos >= DEDUP_NEAR_DUP and self._llm is not None and self._dedup_judge(cand, top_id):
            return top_id
        return None

    def _dedup_judge(self, cand: Note, existing_id: str) -> bool:
        existing = self._store.get_notes([existing_id])
        if not existing or self._llm is None:
            return False
        result = self._llm.complete(
            task=TaskTag.DEDUP_BATCH,
            system=DEDUP_SYSTEM,
            user=build_dedup_user(cand.content, existing[0].content),
            schema=ConflictVerdict,
        )
        verdict = result.parsed
        return isinstance(verdict, ConflictVerdict) and verdict.relation == "duplicate"

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
