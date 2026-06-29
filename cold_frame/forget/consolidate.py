"""Consolidator — capacity cap + decay archive (SPEC §6 / I13).

Non-destructive (archive, not delete — I2); pinned AND high-importance never archived.
Convergent at a fixed clock (re-run with the same ``now`` = no-op); progressive decay
across later clocks is expected and bounded by the caps. Archive fires when over a
per-scope/type capacity cap (lowest ``archive_score`` first) OR on decay (display
``S < 0.33`` AND ``archive_score < 0.20``). On top of that deterministic cap/decay core, this module
also performs the LLM episodic→semantic roll-up (``_consolidate_episodic``, LLM-gated) — atomic +
convergent via the ``derived_from`` markers.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from cold_frame.constants import (
    ARCHIVE_PROTECT_IMPORTANCE,
    ARCHIVE_THRESHOLD,
    ARCHIVE_W_IMPORTANCE,
    ARCHIVE_W_RELEVANCE,
    ARCHIVE_W_RETRIEVABILITY,
    BAND_BUDDING,
    CAP_EPISODIC,
    CAP_PROCEDURAL,
    CAP_SEMANTIC,
    CONSOLIDATE_CLUSTER_COSINE,
    CONSOLIDATE_DEMOTE_FACTOR,
)
from cold_frame.exceptions import StoreError
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import ConsolidateResult, Note, Scope, Source
from cold_frame.observability import get_logger
from cold_frame.prompts.consolidate import (
    CONSOLIDATE_SUMMARY_SYSTEM,
    ConsolidationOutput,
    build_consolidate_user,
)
from cold_frame.read.strength import compute_strength
from cold_frame.store.base import Store

_log = get_logger(__name__)


def _opt_iso(dt: datetime | None) -> str:
    # fixed-width fractional seconds → sortable TEXT (see store._to_iso)
    if dt is None:
        return ""
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


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

    def __init__(
        self,
        store: Store,
        *,
        embedder: Embedder,
        llm: LLM | None,
        clock: Clock,
        new_id: Callable[[], str],
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._clock = clock
        self._new_id = new_id

    def consolidate(
        self,
        *,
        scope: Scope,
        now: datetime | None = None,
        caps: dict[str, int] | None = None,
    ) -> ConsolidateResult:
        at = now or self._clock.now()
        caps = {
            **_DEFAULT_CAPS,
            **(caps or {}),
        }  # overlay: a partial override never disables others

        # 1. episodic → semantic merge (LLM only; convergent). Mutates the store, so the
        #    capacity/decay pass below sees the post-consolidation active set.
        merged = self._consolidate_episodic(scope, at) if self._llm is not None else []

        active = self._store.by_status(
            scope=scope, status="active", sort="recent", limit=_LIST_LIMIT
        )
        by_type: dict[str, list[Note]] = defaultdict(list)
        for note in active:
            by_type[note.memory_type].append(note)

        archived: list[str] = []
        for mtype, cap in caps.items():
            notes = by_type.get(mtype, [])
            # I13: pinned AND high-importance are never archived (exempt from the cap)
            candidates = [
                n for n in notes if not n.pinned and n.importance < ARCHIVE_PROTECT_IMPORTANCE
            ]
            ranked = sorted(candidates, key=lambda n: (archive_score(n, at), n.id))

            # capacity cap: archive the lowest-score candidates beyond the cap (count protected
            # against the cap so the active total trends toward `cap`).
            to_archive: list[str] = [n.id for n in ranked[: max(0, len(notes) - cap)]]
            seen = set(to_archive)
            for n in candidates:  # decay archive: weak AND low-value (S<0.33 AND score<0.20)
                if n.id in seen:
                    continue
                weak = compute_strength(n, at).value < BAND_BUDDING
                if weak and archive_score(n, at) < ARCHIVE_THRESHOLD:
                    to_archive.append(n.id)
                    seen.add(n.id)

            for nid in to_archive:  # per-note atomic archive; resilient to a single failure
                try:
                    self._store.archive(nid, now=at)
                    archived.append(nid)
                except StoreError:
                    _log.warning("archive_failed", extra={"note_id_hash": hash(nid)})

        return ConsolidateResult(reinforced=0, merged=merged, archived=archived)

    def _consolidate_episodic(self, scope: Scope, at: datetime) -> list[str]:
        """Cluster same-topic active episodics → one semantic summary each (convergent)."""
        active = self._store.by_status(
            scope=scope, status="active", sort="recent", limit=_LIST_LIMIT
        )
        episodic = [n for n in active if n.memory_type == "episodic"]
        if len(episodic) < 2:
            return []
        # convergence: a note already summarized has an incoming derived_from edge — skip it,
        # so a re-run produces no new merges (idempotent at a fixed state).
        consumed = {
            e.dst_id
            for e in self._store.neighbors([n.id for n in episodic], relations=["derived_from"])
        }  # the store already filtered to derived_from edges
        fresh = [n for n in episodic if n.id not in consumed]
        if len(fresh) < 2:
            return []

        vecs = self._embedder.embed([n.content for n in fresh])  # (m, dim), L2-normalized
        cos = vecs @ vecs.T
        assigned: set[str] = set()
        merged: list[str] = []
        for i, seed in enumerate(fresh):
            if seed.id in assigned:
                continue
            members = [
                fresh[j]
                for j in range(len(fresh))
                if fresh[j].id not in assigned and cos[i, j] >= CONSOLIDATE_CLUSTER_COSINE
            ]
            if len(members) < 2:  # no same-topic neighbors → nothing to consolidate
                continue
            for m in members:
                assigned.add(m.id)
            summary_id = self._summarize_cluster(members, scope, at)
            if summary_id is not None:
                merged.append(summary_id)
        return merged

    def _summarize_cluster(self, members: list[Note], scope: Scope, at: datetime) -> str | None:
        """LLM-summarize one episodic cluster → a semantic note + derived_from + cold-demote."""
        assert self._llm is not None
        cluster = [{"text": m.content, "valid_at": _opt_iso(m.valid_at)} for m in members]
        result = self._llm.complete(
            task=TaskTag.CONSOLIDATE_SUMMARY,
            system=CONSOLIDATE_SUMMARY_SYSTEM,
            user=build_consolidate_user(cluster),
            schema=ConsolidationOutput,
        )
        out = result.parsed
        if not isinstance(out, ConsolidationOutput) or not out.summary:
            return None

        valids = [m.valid_at for m in members if m.valid_at is not None]
        valid_at = _parse_iso(out.valid_at) or (min(valids) if valids else at)
        summary = Note(
            id=self._new_id(),
            content=out.summary,
            memory_type="semantic",  # episodic cluster distilled to a standing fact
            keywords=out.keywords,
            scope=scope,
            created_at=at,
            valid_at=valid_at,
            importance=max(m.importance for m in members),
            confidence=min(m.confidence for m in members),
            sources=[
                Source(
                    kind="manual",
                    ref="consolidate",
                    content_hash=hashlib.sha256(out.summary.encode("utf-8")).hexdigest(),
                    observed_at=at,
                )
            ],
        )
        # spare pinned / high-importance members from the decay bump — they signal "keep prominent",
        # and the capacity/decay archival pass already exempts them; mirror that exemption here.
        demote = [
            m.id for m in members if not m.pinned and m.importance < ARCHIVE_PROTECT_IMPORTANCE
        ]
        # ONE atomic commit (summary grains + derived_from edges + demote): a partial failure must
        # not orphan the summary, else its members re-cluster on the next retry → a duplicate fact.
        self._store.consolidate_commit(
            summary,
            self._embedder.embed_one(summary.content),
            member_ids=[m.id for m in members],
            demote_ids=demote,
            factor=CONSOLIDATE_DEMOTE_FACTOR,
            at=at,
        )
        return summary.id
