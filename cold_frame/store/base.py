"""Store ABC — the ONE canonical merged adapter contract (CLAUDE.md G4).

api-contract §3 and data-layer §9 listed divergent method sets; this is the single
reconciled surface. Where the two docs disagreed, the name chosen by CLAUDE.md's
build task line wins and the divergence is documented inline:

| concern            | api-contract §3 | data-layer §9 | CANONICAL (here)        |
|--------------------|-----------------|---------------|-------------------------|
| reinforce access   | ``touch``       | ``touch``     | ``reinforce`` (task)    |
| job lease          | ``claim_job``   | ``claim_job`` | ``lease_job`` (task)    |
| job success        | ``complete_job``| ``finish_job``| ``finish_job`` (task)   |
| job failure        | ``fail_job``    | ``finish_job``| ``fail_job`` (task)     |
| secret hard-purge  | ``purge_note``  | ``purge``     | ``purge`` (task)        |
| embedding type     | ``list[float]`` | ``bytes``     | ``np.ndarray`` (G3)     |
| status change      | ``set_status``  | ``archive``   | ``set_status`` (general)|

``emb`` is ``np.ndarray | None`` to match ``Embedder.embed`` (G3); the SQLite adapter
serializes it to a float32 BLOB. ALL writes are ONE transaction (I3): notes + note_fts
+ note_vec + sources + note_history + events co-written, ``BEGIN IMMEDIATE``…``COMMIT``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from cold_frame.llm.base import EmbedderMeta
from cold_frame.models import (
    Edge,
    EdgeRelation,
    Note,
    Scope,
    StatusLiteral,
    UpdateType,
)


class Job(BaseModel):
    """A durable background job row (I12; data-layer §1)."""

    id: str
    kind: str  # consolidate | reembed | conflict_llm | dedup_llm
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "running", "done", "failed", "dead"] = "pending"
    attempts: int = 0
    max_attempts: int = 5
    dedup_key: str | None = None
    run_after: datetime
    locked_by: str | None = None
    locked_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class Event(BaseModel):
    """A co-written append-only audit/sync log row (D17; data-layer §1)."""

    event_id: str  # uuid4 (idempotency key)
    device_id: str
    hlc: str  # "<millis>:<counter>:<device_id>" — lexically sortable
    entity: Literal["note", "edge"]
    entity_id: str  # note.id or "src|rel|dst"
    op: Literal["create", "update", "archive", "delete", "purge"]
    content_hash: str | None = None
    payload: str  # json of the change (NULL/scrubbed for secret purge)
    ts: datetime


class PurgeReport(BaseModel):
    """Proof returned by ``purge`` (secret hard-purge, §7) — grep-verification result."""

    note_id: str
    rows_scrubbed: int
    grep_clean: bool  # original token absent from .db/.db-wal/export (N/A under SQLCipher)
    vacuumed: bool


class Store(ABC):
    """Synchronous adapter seam (D10). Every method raises ``StoreError`` on driver failure."""

    # ── lifecycle ──────────────────────────────────────────────────────────
    @abstractmethod
    def migrate(self) -> None:
        """Idempotent: create notes/note_fts/note_vec/edges/note_history/sources/
        access_log/events/jobs/meta; write embedder_meta on first run if absent."""
        ...

    @abstractmethod
    def embedder_meta(self) -> EmbedderMeta | None:
        """Return {embedder_id, dim} from the ``meta`` table; None on a fresh db."""
        ...

    @abstractmethod
    def set_embedder_meta(self, meta: EmbedderMeta) -> None: ...

    # ── re-embedding migration (I8/I10: swap embedder → bring stale vectors current) ──
    @abstractmethod
    def stale_vector_notes(self, *, current_id: str) -> list[Note]:
        """Notes whose stored vector was written by a DIFFERENT embedder than ``current_id``
        (so KNN currently excludes them, I10). The work-list for ``reembed``."""
        ...

    @abstractmethod
    def reembed(self, items: list[tuple[str, np.ndarray]], *, meta: EmbedderMeta) -> int:
        """Replace each note's vector with a fresh ``(id, emb)``, retag ``notes.embedder_id`` to
        ``meta.embedder_id``, AND flip the stored embedder_meta — all in ONE txn (I3), so a crash
        can't leave meta lagging the retag. Returns the count. Empty ``items`` is valid: it just
        re-syncs the stored meta to ``meta``."""
        ...

    @abstractmethod
    def get_meta(self, key: str) -> str | None: ...

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None: ...

    @abstractmethod
    def in_transaction(self) -> AbstractContextManager[None]:
        """Explicit ``BEGIN IMMEDIATE`` txn for WriteCore multi-step commits (I3)."""
        ...

    # ── atomic write (ALL grains in one txn, I3) ────────────────────────────
    @abstractmethod
    def add_note(self, note: Note, emb: np.ndarray | None) -> None:
        """INSERT notes + note_fts + note_vec(if emb) + sources + note_history(v1)
        + co-written event row — ONE transaction. ``emb=None`` allowed (no-embed)."""
        ...

    @abstractmethod
    def update_note(
        self, note: Note, *, update_type: UpdateType, emb: np.ndarray | None = None
    ) -> None:
        """In-place edit + note_history snapshot + ``update`` event, ONE txn. Optimistically
        version-locked (compare-and-swap): ``note.version`` MUST be the persisted version + 1;
        a stale version (a concurrent write won) raises ``StoreError``. NoteNotFound if absent."""
        ...

    @abstractmethod
    def supersede(self, old_id: str, new: Note, emb: np.ndarray | None) -> None:
        """Conflict/correct commit in ONE txn: old→archived + invalid_at=now +
        ``supersedes`` edge new→old + note_history snapshot + insert new(active)."""
        ...

    @abstractmethod
    def get_notes(self, ids: list[str]) -> list[Note]:
        """Order preserved; unknown ids skipped silently."""
        ...

    @abstractmethod
    def set_status(
        self, id: str, status: StatusLiteral, *, invalid_at: datetime | None = None
    ) -> None: ...

    @abstractmethod
    def archive(self, id: str, *, now: datetime) -> None:
        """Soft-archive a note (forgetting/cap): status→archived + expired_at=now (txn-time
        end) + co-written ``archive`` event + note_history, ONE txn (I2/I3/I17). Valid-time
        (valid_at/invalid_at) is unchanged — the fact isn't false, we just stopped keeping it."""
        ...

    @abstractmethod
    def revive(self, id: str) -> None:
        """Un-archive (status→active, clear invalid_at/expired_at) + co-written event (I2)."""
        ...

    @abstractmethod
    def set_pinned(self, id: str, pinned: bool) -> None:
        """Set the pin flag; pinned notes are exempt from decay/archive (I13)."""
        ...

    @abstractmethod
    def delete(self, id: str) -> None:
        """Hard-delete a note + its searchable grains (notes/fts/vec/sources/history/edges) in ONE
        txn + a co-written ``delete`` event. NOT the secret-scrub (that's ``purge``): the prior
        append-only event payloads are retained. NoteNotFound if absent."""
        ...

    @abstractmethod
    def cold_demote(self, ids: list[str], *, factor: float) -> None:
        """Multiply decay_S by ``factor`` (consolidation cold-demote — sources fade faster)."""
        ...

    # ── retrieval ───────────────────────────────────────────────────────────
    @abstractmethod
    def knn(
        self,
        emb: np.ndarray,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        """[(note_id, cosine)]. Hard-filters ``embedder_id=current`` (I10). [] on no match."""
        ...

    @abstractmethod
    def bm25(
        self,
        query: str,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        """[(note_id, raw_bm25)]. [] on no match."""
        ...

    @abstractmethod
    def reinforce(self, ids: list[str], *, now: datetime) -> None:
        """REINFORCE (SPEC §5 step 6): access_count++, last_accessed=now, decay_S +=
        REINFORCE_DECAY_INC, AND insert capped access_log row(s). (api-contract: ``touch``)."""
        ...

    # ── edges ─────────────────────────────────────────────────────────────
    @abstractmethod
    def add_edge(self, edge: Edge) -> None: ...

    @abstractmethod
    def neighbors(
        self, ids: list[str], *, relations: list[EdgeRelation] | None = None
    ) -> list[Edge]:
        """1-hop edges touching any of ``ids`` (optionally filtered by relation)."""
        ...

    # ── triage / quarantine reads (G2 flag-column model) ───────────────────
    @abstractmethod
    def held_for_human(self, *, scope: Scope, limit: int) -> list[Note]:
        """Active notes with ``held_for_human=True`` (the Triage queue), ranked importance-first.
        Archived/resolved holds are excluded — clearing the flag is what removes an item."""
        ...

    @abstractmethod
    def set_held_for_human(
        self, id: str, *, held: bool, quarantined: bool, reason: str | None
    ) -> None:
        """Set the G2 flag columns (held_for_human / quarantined / triage_reason)."""
        ...

    @abstractmethod
    def by_status(
        self,
        *,
        scope: Scope,
        status: StatusLiteral,
        sort: Literal["decay", "recent", "importance"],
        limit: int,
        offset: int = 0,
    ) -> list[Note]: ...

    @abstractmethod
    def as_of(self, ids: list[str], *, at: datetime) -> list[Note]:
        """Bi-temporal snapshot of ``ids`` valid at ``at`` (valid_at<=at<invalid_at)."""
        ...

    # ── jobs (durable queue, I12; task: lease / finish / fail) ──────────────
    @abstractmethod
    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
        run_after: datetime | None = None,
    ) -> str:
        """Insert a job; ``dedup_key`` debounces (one pending per key). Returns job id."""
        ...

    @abstractmethod
    def lease_job(self, *, worker: str, now: datetime) -> Job | None:
        """Claim one runnable job (status=running, locked_by/at, attempts++); stale-reclaims
        running jobs past LEASE_TTL. None if nothing runnable. (api-contract: ``claim_job``)."""
        ...

    @abstractmethod
    def finish_job(self, id: str, *, worker: str) -> None:
        """Mark a leased job done — fenced on ``locked_by==worker`` (zombie-worker guard, I12)."""
        ...

    @abstractmethod
    def fail_job(self, id: str, *, error: str, retry_after: datetime | None, worker: str) -> None:
        """Reschedule (attempts<max → pending+backoff) or dead-letter; fenced on ``worker``."""
        ...

    @abstractmethod
    def pending_count(self, kind: str | None = None) -> int:
        """Count pending jobs (optionally of one ``kind``) — observability for doctor (I12)."""
        ...

    # ── event log / export (D17) ────────────────────────────────────────────
    @abstractmethod
    def append_event(self, ev: Event) -> None:
        """Called INSIDE the add_note/update_note/supersede txn (co-write, never alone)."""
        ...

    @abstractmethod
    def get_history(self, id: str) -> list[Note]:
        """All persisted versions of ``id`` (oldest→newest) from note_history."""
        ...

    @abstractmethod
    def access_log(self, id: str, *, limit: int = 50) -> list[datetime]:
        """Recall timestamps for ``id`` (oldest→newest) from the capped access_log (I13)."""
        ...

    @abstractmethod
    def iter_events(self, *, since_hlc: str | None = None) -> Iterator[Event]: ...

    @abstractmethod
    def snapshot(self, dst: str) -> None:
        """Consistent whole-DB backup to ``dst`` (I17: a checkpointed snapshot, never live WAL)."""
        ...

    # ── secret hard-purge (the ONLY append-only carve-out, I2/§7) ───────────
    @abstractmethod
    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        """Scrub a secret/PII note across ALL grains in one txn + VACUUM + grep-verify.
        (api-contract: ``purge_note``)."""
        ...
