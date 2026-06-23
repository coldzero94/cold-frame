"""WriteCore — the single persist path (I15, D8).

Leaf stub. Every entry runs the SAME pipeline: ADMISSION (CLASSIFY → REDACT →
CONFIDENCE-GATE → CONSENT) → DEDUP → CONFLICT → PERSIST, in ONE Store transaction (I3).
Bodies raise ``NotImplementedError``; P1+ fill them in without changing signatures.

- ``commit`` is used by ``add()`` and ``create_fact``.
- ``commit_supersede`` is used by ``correct_memory``, ``update_fact``, ``supersede`` —
  keyed by explicit id (NOT similarity search), the same commit as the conflict path.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from cold_frame.constants import CONFLICT_CANDIDATE_FLOOR, DEDUP_AUTO_MERGE, DEDUP_NEAR_DUP
from cold_frame.exceptions import SecretBlocked
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import AddResult, BlockedSpan, ConflictVerdict, Note, Scope, Source
from cold_frame.prompts.conflict import (
    CONFLICT_SYSTEM,
    DEDUP_SYSTEM,
    build_conflict_user,
    build_dedup_user,
)
from cold_frame.store.base import Store
from cold_frame.write.admission import scan_secret


def _iso_or_unknown(dt: datetime | None) -> str:
    return dt.isoformat().replace("+00:00", "Z") if dt is not None else "unknown"


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

        ADMISSION (v1, D25) = a deterministic secret scan: an obvious secret/credential is
        BLOCKed pre-disk (never embedded, never persisted, never sent to the host) and reported
        in ``blocked`` as a content-free placeholder (I6). REDACT/CONSENT + local tiebreak (I7)
        are deferred. Each surviving candidate is then classified against the nearest active
        note: DEDUP (cosine≥0.93 auto-merge, [0.82,0.93) ambiguous → DEDUP LLM) → CONFLICT
        (LLM proposes contradiction) → deterministic freshness (valid_at, NEVER the LLM — I1) →
        supersede / stale-mark / triage. Quarantined/held candidates route to ``held``.
        """
        added: list[Note] = []
        held: list[Note] = []
        deduped: list[str] = []
        superseded: list[str] = []
        blocked: list[BlockedSpan] = []
        for cand in candidates:
            verdict = scan_secret(cand.content)
            if verdict is not None:  # I6: a secret never touches disk (no embed, no host call)
                blocked.append(BlockedSpan(reason=verdict[0], placeholder=verdict[1]))
                continue
            emb = self._embedder.embed_one(cand.content)
            kind, payload = self._classify(cand, emb, scope)
            if kind == "dedup":
                deduped.append(str(payload))  # non-destructive: drop the dup, existing stays
            elif kind == "supersede":
                self._store.supersede(str(payload), cand, emb)
                superseded.append(str(payload))
                added.append(cand)
            elif kind == "stale":
                # the new fact is OLDER than the current belief → persist but bound it
                # (Graphiti rule: new.invalid_at = old.valid_at); never archives the old.
                stale = cand.model_copy(update={"invalid_at": payload})
                self._store.add_note(stale, emb)
                added.append(stale)
            elif kind == "held":
                tied = cand.model_copy(
                    update={
                        "held_for_human": True,
                        "quarantined": True,
                        "triage_reason": "true_conflict",
                    }
                )
                self._store.add_note(tied, emb)
                held.append(tied)
            else:  # "add"
                self._store.add_note(cand, emb)
                (held if cand.held_for_human or cand.quarantined else added).append(cand)
        return AddResult(
            added=added, superseded=superseded, deduped=deduped, blocked=blocked, held=held
        )

    def _classify(self, cand: Note, emb: np.ndarray, scope: Scope) -> tuple[str, object]:
        """Decide a candidate's fate vs its active neighbors (SPEC §4 DEDUP→CONFLICT).

        Walks the nearest active notes in descending cosine (not just hits[0]): a true
        same-subject contradiction can sit at rank 2+ when a higher-cosine neighbor is a
        near-dup-not-duplicate. ≥0.93 auto-merge; [0.82,0.93) dedup judge (duplicate→merge,
        else keep scanning — not escalated to conflict, contradictions sit below the band);
        [floor,0.82) conflict judge (duplicate→merge, contradiction→deterministic freshness,
        else keep scanning); below floor → stop (hits are cosine-descending).
        """
        for nid, cos in self._store.knn(emb, 5, scope=scope, statuses=["active"]):
            if cos >= DEDUP_AUTO_MERGE:
                return ("dedup", nid)
            if cos >= DEDUP_NEAR_DUP:
                if self._llm is not None and self._dedup_judge(cand, nid):
                    return ("dedup", nid)
                continue  # near-dup but not a duplicate → distinct; scan the next neighbor
            if cos >= CONFLICT_CANDIDATE_FLOOR:
                if self._llm is None:
                    continue  # offline: no contradiction judging
                existing = self._store.get_notes([nid])
                if not existing:
                    continue
                relation = self._conflict_judge(cand, existing[0])
                if relation == "duplicate":
                    return ("dedup", nid)
                if relation == "contradiction":
                    return self._freshness(cand, existing[0])
                continue  # unrelated → scan the next neighbor
            break  # cosine-descending: nothing below the conflict floor is worth judging
        return ("add", None)

    @staticmethod
    def _freshness(cand: Note, old: Note) -> tuple[str, object]:
        """DETERMINISTIC freshness (I1): valid_at decides supersession — never the LLM."""
        cv, ov = cand.valid_at, old.valid_at
        if cv is None or ov is None or cv == ov:
            return ("held", None)  # tie / no time signal → Triage (true_conflict)
        if cv > ov:
            return ("supersede", old.id)  # new is newer → archive old, persist new
        return ("stale", ov)  # new is older → persist new bounded by old.valid_at

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

    def _conflict_judge(self, cand: Note, existing: Note) -> str:
        if self._llm is None:
            return "unrelated"
        result = self._llm.complete(
            task=TaskTag.CONFLICT_JUDGE,
            system=CONFLICT_SYSTEM,
            user=build_conflict_user(
                cand.content,
                _iso_or_unknown(cand.valid_at),
                existing.content,
                _iso_or_unknown(existing.valid_at),
            ),
            schema=ConflictVerdict,
        )
        verdict = result.parsed
        return verdict.relation if isinstance(verdict, ConflictVerdict) else "unrelated"

    def commit_supersede(
        self,
        old_id: str,
        new: Note,
        *,
        reason: str,
    ) -> Note:
        """Explicit-id supersede (correct/update_fact/supersede): old→archived +
        invalid_at=new.valid_at + ``supersedes`` edge new→old + note_history, ONE txn (I3).

        Keyed by an EXPLICIT id (NOT a similarity search) — the same Store.supersede commit
        the conflict path uses (I15). ADMISSION (v1): a secret in ``new`` raises ``SecretBlocked``
        (strict path — this returns a single Note, so there is no ``blocked`` list to report in).
        """
        verdict = scan_secret(new.content)
        if verdict is not None:  # I6: never persist a secret, even via an explicit self-edit
            raise SecretBlocked(verdict[1])
        emb = self._embedder.embed_one(new.content)
        self._store.supersede(old_id, new, emb)
        return new
