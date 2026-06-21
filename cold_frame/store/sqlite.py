"""SQLiteStore — the v1 ``Store`` adapter (single ``.db`` file).

Leaf stub: every method is typed to match the ``Store`` ABC exactly and raises
``NotImplementedError``. P1 fills these in (DDL, one-txn dual-write, FTS5/numpy-KNN,
jobs queue) WITHOUT changing signatures. Dialect-specific bits (FTS5/sqlite-vec/JSON)
stay behind this adapter (I8); core never imports sqlite-specific idioms.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal

import numpy as np

from cold_frame.llm.base import Embedder, EmbedderMeta
from cold_frame.models import (
    Edge,
    EdgeRelation,
    Note,
    Scope,
    StatusLiteral,
    UpdateType,
)
from cold_frame.store.base import Event, Job, PurgeReport, Store


class SQLiteStore(Store):
    """Single-file SQLite adapter (one ``.db``: notes + FTS + vectors + edges + jobs)."""

    def __init__(self, db_path: str, *, embedder: Embedder | None = None) -> None:
        # P1: open the connection (WAL, busy_timeout, secure_delete), keep embedder for
        # dim/meta. Stub stores config only; no connection opened yet.
        self._db_path = db_path
        self._embedder = embedder

    # ── lifecycle ──────────────────────────────────────────────────────────
    def migrate(self) -> None:
        raise NotImplementedError

    def embedder_meta(self) -> EmbedderMeta | None:
        raise NotImplementedError

    def set_embedder_meta(self, meta: EmbedderMeta) -> None:
        raise NotImplementedError

    def get_meta(self, key: str) -> str | None:
        raise NotImplementedError

    def set_meta(self, key: str, value: str) -> None:
        raise NotImplementedError

    def in_transaction(self) -> AbstractContextManager[None]:
        raise NotImplementedError

    # ── atomic write ────────────────────────────────────────────────────────
    def add_note(self, note: Note, emb: np.ndarray | None) -> None:
        raise NotImplementedError

    def update_note(
        self, note: Note, *, update_type: UpdateType, emb: np.ndarray | None = None
    ) -> None:
        raise NotImplementedError

    def supersede(self, old_id: str, new: Note, emb: np.ndarray | None) -> None:
        raise NotImplementedError

    def get_notes(self, ids: list[str]) -> list[Note]:
        raise NotImplementedError

    def set_status(
        self, id: str, status: StatusLiteral, *, invalid_at: datetime | None = None
    ) -> None:
        raise NotImplementedError

    # ── retrieval ───────────────────────────────────────────────────────────
    def knn(
        self,
        emb: np.ndarray,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    def bm25(
        self,
        query: str,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    def reinforce(self, ids: list[str], *, now: datetime) -> None:
        raise NotImplementedError

    # ── edges ─────────────────────────────────────────────────────────────
    def add_edge(self, edge: Edge) -> None:
        raise NotImplementedError

    def neighbors(
        self, ids: list[str], *, relations: list[EdgeRelation] | None = None
    ) -> list[Edge]:
        raise NotImplementedError

    # ── triage / quarantine reads ───────────────────────────────────────────
    def held_for_human(self, *, scope: Scope, limit: int) -> list[Note]:
        raise NotImplementedError

    def set_held_for_human(
        self, id: str, *, held: bool, quarantined: bool, reason: str | None
    ) -> None:
        raise NotImplementedError

    def by_status(
        self,
        *,
        scope: Scope,
        status: StatusLiteral,
        sort: Literal["decay", "recent", "importance"],
        limit: int,
        offset: int = 0,
    ) -> list[Note]:
        raise NotImplementedError

    def as_of(self, ids: list[str], *, at: datetime) -> list[Note]:
        raise NotImplementedError

    # ── jobs (durable queue) ────────────────────────────────────────────────
    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
        run_after: datetime | None = None,
    ) -> str:
        raise NotImplementedError

    def lease_job(self, *, worker: str, now: datetime) -> Job | None:
        raise NotImplementedError

    def finish_job(self, id: str) -> None:
        raise NotImplementedError

    def fail_job(self, id: str, *, error: str, retry_after: datetime | None) -> None:
        raise NotImplementedError

    # ── event log / export ──────────────────────────────────────────────────
    def append_event(self, ev: Event) -> None:
        raise NotImplementedError

    def iter_events(self, *, since_hlc: str | None = None) -> Iterator[Event]:
        raise NotImplementedError

    # ── secret hard-purge ───────────────────────────────────────────────────
    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        raise NotImplementedError
