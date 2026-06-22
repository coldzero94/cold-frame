"""Consolidator — capacity cap + decay archive (SPEC §6 / I13).

Non-destructive (archive, not delete — I2), convergent (re-run = no-op), pinned never
archived. Archive fires when over a per-scope/type capacity cap (lowest ``archive_score``
first) OR on decay (display ``S < 0.33`` AND ``archive_score < 0.20``). The LLM episodic→
semantic summary is P4-2; this is the deterministic forgetting core.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from cold_frame.constants import (
    ARCHIVE_THRESHOLD,
    ARCHIVE_W_IMPORTANCE,
    ARCHIVE_W_RELEVANCE,
    ARCHIVE_W_RETRIEVABILITY,
    BAND_BUDDING,
    CAP_EPISODIC,
    CAP_PROCEDURAL,
    CAP_SEMANTIC,
)
from cold_frame.llm.base import LLM, Clock
from cold_frame.models import ConsolidateResult, Note, Scope
from cold_frame.read.strength import compute_strength
from cold_frame.store.base import Store

_DEFAULT_CAPS: dict[str, int] = {
    "semantic": CAP_SEMANTIC,
    "episodic": CAP_EPISODIC,
    "procedural": CAP_PROCEDURAL,
}
_LIST_LIMIT = 100_000  # personal scale; fetch all active for the per-type cap pass


def archive_score(note: Note, now: datetime, *, relevance: float = 0.0) -> float:
    """Consolidation/archive ranking signal (NOT display strength; api-contract §4)."""
    ref = note.last_accessed or note.created_at
    dt_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    retrievability = math.exp(-dt_days / max(note.decay_S, 1e-9))
    return (
        ARCHIVE_W_RETRIEVABILITY * retrievability
        + ARCHIVE_W_IMPORTANCE * note.importance
        + ARCHIVE_W_RELEVANCE * relevance
    )


class Consolidator:
    """Forgetting-curve maintenance: archive over-cap / decayed notes (SPEC §6)."""

    def __init__(self, store: Store, *, llm: LLM | None, clock: Clock) -> None:
        self._store = store
        self._llm = llm
        self._clock = clock

    def consolidate(
        self,
        *,
        scope: Scope,
        now: datetime | None = None,
        caps: dict[str, int] | None = None,
    ) -> ConsolidateResult:
        at = now or self._clock.now()
        caps = caps or _DEFAULT_CAPS
        active = self._store.by_status(
            scope=scope, status="active", sort="recent", limit=_LIST_LIMIT
        )
        by_type: dict[str, list[Note]] = defaultdict(list)
        for note in active:
            by_type[note.memory_type].append(note)

        archived: list[str] = []
        for mtype, cap in caps.items():
            notes = by_type.get(mtype, [])
            candidates = [n for n in notes if not n.pinned]  # pinned exempt (I13)
            ranked = sorted(candidates, key=lambda n: (archive_score(n, at), n.id))

            to_archive: list[str] = [n.id for n in ranked[: max(0, len(notes) - cap)]]
            seen = set(to_archive)
            for n in candidates:  # decay archive: weak AND low-value (S<0.33 AND score<0.20)
                if n.id in seen:
                    continue
                weak = compute_strength(n, at).value < BAND_BUDDING
                if weak and archive_score(n, at) < ARCHIVE_THRESHOLD:
                    to_archive.append(n.id)
                    seen.add(n.id)

            for nid in to_archive:
                self._store.set_status(nid, "archived")
            archived.extend(to_archive)

        return ConsolidateResult(reinforced=0, merged=[], archived=archived)
