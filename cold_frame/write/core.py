"""WriteCore — the single persist path (I15, D8).

Every entry runs the SAME pipeline: ADMISSION → DEDUP → CONFLICT → PERSIST, in ONE Store
transaction (I3). ADMISSION (``_admission_block``) = a deterministic secret-BLOCK + a LOCAL-only
LLM tiebreak for ambiguous spans (I6/I7); CONFIDENCE-GATE/CONSENT are deferred (D25).

- ``commit`` is used by ``add()`` and ``create_fact``.
- ``commit_supersede`` is used by ``correct_memory``, ``update_fact``, ``supersede`` —
  keyed by explicit id (NOT similarity search), the same commit as the conflict path.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

import numpy as np

from cold_frame.constants import CONFLICT_CANDIDATE_FLOOR, DEDUP_AUTO_MERGE, DEDUP_NEAR_DUP
from cold_frame.exceptions import PolicyError, SecretBlocked
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import (
    AddResult,
    BlockedSpan,
    ConflictVerdict,
    Note,
    PiiCategory,
    RedactedSpan,
    Scope,
)
from cold_frame.observability import get_logger
from cold_frame.prompts.admission import (
    ADMISSION_SYSTEM,
    AdmissionVerdict,
    build_admission_user,
)
from cold_frame.prompts.conflict import (
    CONFLICT_SYSTEM,
    DEDUP_SYSTEM,
    build_conflict_user,
    build_dedup_user,
)
from cold_frame.store.base import Store
from cold_frame.write.admission import Verdict, ambiguous_spans, redact_pii, scan_secret
from cold_frame.write.extract import _sha256  # re-hash sources over redacted content (PII scrub)

_log = get_logger(__name__)


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
        pii_redact: frozenset[PiiCategory] | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._clock = clock
        # OPT-IN PII categories to scrub inline pre-disk (None = off; see admission.redact_pii)
        self._pii_redact = pii_redact

    def _admission_block(self, content: str) -> Verdict | None:
        """``(reason, placeholder)`` if ``content`` must be BLOCKed pre-disk, else None (I6/I7).

        Deterministic secret scan FIRST (obvious secret → BLOCK). Then, for an AMBIGUOUS span, a
        LOCAL-only LLM tiebreak that fails CLOSED. The tiebreak runs whenever an LLM IS configured:
        a non-local one is rejected before the call (assert_local_for → PolicyError → BLOCK; never
        sent to a remote endpoint), a local one judges it (judged-secret / unparseable / call
        error ⇒ BLOCK). Only with NO LLM is there no tiebreak — the deterministic gate stands, the
        candidate proceeds (I5). All BLOCK returns use reason="ambiguous" (not a confirmed match).
        """
        verdict = scan_secret(content)
        if verdict is not None:
            return verdict
        spans = ambiguous_spans(content)
        if not spans or self._llm is None:
            return None
        try:
            self._llm.assert_local_for(TaskTag.ADMISSION_TIEBREAK)  # I7: local-only or PolicyError
        except PolicyError:
            _log.info("admission_blocked", extra={"reason": "ambiguous_remote_llm"})
            return (
                "ambiguous",
                "[BLOCKED:ambiguous_remote_llm]",
            )  # never send a span remote → block
        for span in spans:
            try:
                res = self._llm.complete(
                    task=TaskTag.ADMISSION_TIEBREAK,
                    system=ADMISSION_SYSTEM,
                    user=build_admission_user(span),
                    schema=AdmissionVerdict,
                )
            except Exception as exc:  # tiebreak call failed → can't confirm safe → fail CLOSED
                _log.warning("admission_tiebreak_error", extra={"exc_type": type(exc).__name__})
                return ("ambiguous", "[BLOCKED:ambiguous_tiebreak_error]")
            v = res.parsed
            if not isinstance(v, AdmissionVerdict) or v.is_secret:
                _log.info("admission_blocked", extra={"reason": "ambiguous"})
                return ("ambiguous", "[BLOCKED:ambiguous]")  # unparseable / judged-secret → block
        return None  # the local LLM cleared every ambiguous span

    def _redact(self, note: Note) -> tuple[Note, Counter[PiiCategory]]:
        """OPT-IN PII scrub of EVERY persisted free-text grain — content, context, AND keywords (all
        stored + FTS-indexed; redacting content alone would leak PII to disk + search). On any
        redaction, rebuild each source's content_hash over the REDACTED content so no SHA of the
        original PII lingers (like notes.content_hash, which hashes the redacted text)."""
        assert self._pii_redact is not None
        summ: Counter[PiiCategory] = Counter()
        content, s = redact_pii(note.content, self._pii_redact)
        summ.update(s)
        context, s = redact_pii(note.context, self._pii_redact)
        summ.update(s)
        keywords: list[str] = []
        for kw in note.keywords:
            clean_kw, s = redact_pii(kw, self._pii_redact)
            summ.update(s)
            keywords.append(clean_kw)
        if not summ:
            return note, summ
        sources = [
            src.model_copy(update={"content_hash": _sha256(content)}) for src in note.sources
        ]
        scrubbed = note.model_copy(
            update={
                "content": content,
                "context": context,
                "keywords": keywords,
                "sources": sources,
            }
        )
        return scrubbed, summ

    def commit(
        self,
        candidates: list[Note],
        *,
        scope: Scope,
        reinforce_dedup: bool = True,
    ) -> AddResult:
        """ADMISSION → DEDUP → CONFLICT → PERSIST for new candidate facts (SPEC §4).

        ADMISSION = a deterministic secret scan (obvious secret → BLOCK pre-disk, content-free
        placeholder in ``blocked``, I6) PLUS, for an ambiguous span, a LOCAL-only LLM tiebreak that
        fails CLOSED (I7, see ``_admission_block``). CONFIDENCE-GATE/CONSENT remain deferred (D25).
        Each surviving candidate is then classified against the nearest active
        note: DEDUP (cosine≥0.93 auto-merge, [0.82,0.93) ambiguous → DEDUP LLM) → CONFLICT
        (LLM proposes contradiction) → deterministic freshness (valid_at, NEVER the LLM — I1) →
        supersede / stale-mark / triage. Quarantined/held candidates route to ``held``.
        """
        added: list[Note] = []
        held: list[Note] = []
        deduped: list[str] = []
        superseded: list[str] = []
        blocked: list[BlockedSpan] = []
        pii: Counter[PiiCategory] = Counter()
        for cand in candidates:
            verdict = self._admission_block(cand.content)
            if verdict is not None:  # I6: a secret never touches disk (no embed, no host call)
                blocked.append(BlockedSpan(reason=verdict[0], placeholder=verdict[1]))
                continue
            if self._pii_redact:  # opt-in: scrub PII from ALL grains BEFORE embed/persist
                cand, summ = self._redact(cand)
                pii.update(summ)
            emb = self._embedder.embed_one(cand.content)
            kind, payload = self._classify(cand, emb, scope)
            if kind == "dedup":
                deduped.append(str(payload))  # non-destructive: drop the dup, existing stays
                # a restatement IS a reinforcement signal — bump the survivor so "the user keeps
                # saying this" raises its strength + resists forgetting (dogfood fix). Suppressed on
                # the at-least-once capture path (reinforce_dedup=False): that handler must be
                # idempotent (I12) — a job retry would otherwise double-count the bump. Capture does
                # its own watermark-guarded reinforce of exact restatements instead.
                if reinforce_dedup:
                    self._store.reinforce([str(payload)], now=cand.created_at)
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
            added=added,
            superseded=superseded,
            deduped=deduped,
            blocked=blocked,
            redacted=[RedactedSpan(category=k, count=v) for k, v in pii.items()],
            held=held,
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
        verdict = self._admission_block(new.content)
        if verdict is not None:  # I6: never persist a secret, even via an explicit self-edit
            raise SecretBlocked(verdict[1])
        if self._pii_redact:  # same all-grain PII scrub on the correction (one pipeline, I15)
            new, _ = self._redact(new)
        emb = self._embedder.embed_one(new.content)
        self._store.supersede(old_id, new, emb)
        return new
