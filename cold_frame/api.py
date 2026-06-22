"""``Memory`` facade (api-contract §2) — the single public Python entrypoint.

This is the canonical SIGNATURE surface (G6 bakes the Clock/id-factory into __init__).
Method bodies are scaffold stubs raising ``NotImplementedError``; downstream phases
(P1+) fill them in WITHOUT changing these signatures.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal, TypedDict

from cold_frame.branding import DB_PATH
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound
from cold_frame.llm.base import LLM, Clock, Embedder, HashEmbedder, SystemClock
from cold_frame.models import (
    AddResult,
    ConsolidateResult,
    CorrectResult,
    Edge,
    EdgeRelation,
    Note,
    ProceduralResult,
    Scope,
    SearchResult,
    Source,
    Strength,
    ToolSpec,
    TriageItem,
)
from cold_frame.read.retrieve import RetrievePipeline
from cold_frame.store.sqlite import SQLiteStore
from cold_frame.write.core import WriteCore
from cold_frame.write.extract import extract

__all__ = ["Memory", "Msg"]


class Msg(TypedDict):
    """A chat message handed to ``Memory.add`` (api-contract §2.1)."""

    role: str
    content: str


class Memory:
    """The single public Python entrypoint (api-contract §2). All methods are sync (I4)."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        embedder: Embedder | None = None,
        llm: LLM | None = None,
        default_scope: Scope | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        config: object | None = None,
    ) -> None:
        # Open Store, run migrate() (idempotent), assert the configured embedder's dim
        # matches DB meta else raise EmbedderMismatchError. Clock + id-factory injected (G6):
        # default offline = HashEmbedder + llm=None + SystemClock + uuid4 ids (I5).
        self._db_path = db_path or str(DB_PATH)
        self._embedder: Embedder = embedder or HashEmbedder()
        self._llm = llm
        self._default_scope = default_scope or Scope()
        self._clock: Clock = clock or SystemClock()
        self._new_id: Callable[[], str] = id_factory or (lambda: uuid.uuid4().hex)
        self._config = config

        self._store = SQLiteStore(
            self._db_path, embedder=self._embedder, clock=self._clock, new_id=self._new_id
        )
        self._store.migrate()
        stored = self._store.embedder_meta()
        if stored is not None and stored.dim != self._embedder.meta.dim:
            raise EmbedderMismatchError(
                f"configured embedder dim {self._embedder.meta.dim} != DB meta dim {stored.dim}"
            )
        self._write = WriteCore(
            self._store, embedder=self._embedder, llm=self._llm, clock=self._clock
        )
        self._read = RetrievePipeline(
            self._store, embedder=self._embedder, llm=self._llm, clock=self._clock
        )

    # ── write ────────────────────────────────────────────────────────────
    def add(
        self,
        messages: list[Msg] | str,
        *,
        scope: Scope | None = None,
        infer: bool = True,
        observed_at: datetime | None = None,
        source: Source | None = None,
        raw: bool = False,
    ) -> AddResult:
        scope = scope or self._default_scope
        observed_at = observed_at or self._clock.now()
        candidates = extract(
            messages,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
            observed_at=observed_at,
            scope=scope,
            source=source,
            infer=infer,
            raw=raw,
        )
        return self._write.commit(candidates, scope=scope, source=source)

    def correct_memory(
        self, id: str, new_text: str, *, scope: Scope | None = None
    ) -> CorrectResult:
        raise NotImplementedError

    def update(self, id: str, **fields: object) -> Note:
        raise NotImplementedError

    def delete(self, id: str, *, force: bool = False) -> None:
        raise NotImplementedError

    def pin(self, id: str) -> Note:
        raise NotImplementedError

    def forget(self, id: str) -> Note:
        raise NotImplementedError

    def revive(self, id: str) -> Note:
        raise NotImplementedError

    # ── read ─────────────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        k: int = 10,
        token_budget: int | None = None,
        as_of: datetime | None = None,
        include_archived: bool = False,
        rerank: bool = False,
    ) -> SearchResult:
        return self._read.search(
            query,
            scope=scope or self._default_scope,
            k=k,
            token_budget=token_budget,
            as_of=as_of,
            include_archived=include_archived,
            rerank=rerank,
        )

    def get(self, id: str) -> Note:
        notes = self._store.get_notes([id])
        if not notes:
            raise NoteNotFound(id)
        return notes[0]

    def health(self) -> dict[str, object]:
        """Doctor/health snapshot: invariant counts + integrity + embedder (eval §C.8)."""
        return self._store.doctor()

    def get_many(self, ids: list[str]) -> list[Note]:
        raise NotImplementedError

    def strength(self, id: str) -> Strength:
        raise NotImplementedError

    def neighbors(
        self, id: str, *, relations: list[EdgeRelation] | None = None, hops: int = 1
    ) -> list[Edge]:
        raise NotImplementedError

    def fork_history(self, id: str) -> list[Note]:
        raise NotImplementedError

    # ── maintenance / forgetting ─────────────────────────────────────────
    def consolidate(
        self, *, scope: Scope | None = None, now: datetime | None = None
    ) -> ConsolidateResult:
        raise NotImplementedError

    def triage_queue(self, *, scope: Scope | None = None, limit: int = 50) -> list[TriageItem]:
        raise NotImplementedError

    def resolve_triage(
        self,
        id: str,
        action: Literal["pin", "let_go", "merge", "keep", "supersede"],
        *,
        target: str | None = None,
    ) -> None:
        raise NotImplementedError

    # ── self-edit / procedural ───────────────────────────────────────────
    def memory_tools(self, scope: Scope) -> list[ToolSpec]:
        raise NotImplementedError

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        raise NotImplementedError

    def get_procedural(self, name: str) -> str:
        raise NotImplementedError
