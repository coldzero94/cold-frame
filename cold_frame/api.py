"""``Memory`` facade (api-contract §2) — the single public Python entrypoint.

This is the canonical SIGNATURE surface (G6 bakes the Clock/id-factory into __init__).
Method bodies are scaffold stubs raising ``NotImplementedError``; downstream phases
(P1+) fill them in WITHOUT changing these signatures.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal, TypedDict

from cold_frame.branding import DB_PATH
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound
from cold_frame.forget.consolidate import Consolidator
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
from cold_frame.procedural.optimize import ProceduralOptimizer
from cold_frame.read.retrieve import RetrievePipeline
from cold_frame.read.strength import compute_strength
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
        self._consolidator = Consolidator(
            self._store,
            embedder=self._embedder,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
        )
        self._procedural = ProceduralOptimizer(
            self._store,
            embedder=self._embedder,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
            scope=self._default_scope,
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
        old_notes = self._store.get_notes([id])
        if not old_notes:
            raise NoteNotFound(id)
        old = old_notes[0]
        now = self._clock.now()
        new = Note(
            id=self._new_id(),
            content=new_text,
            memory_type=old.memory_type,
            scope=scope or old.scope,
            created_at=now,
            valid_at=now,  # the correction is true as of now
            importance=old.importance,
            sources=[
                Source(
                    kind="manual",
                    ref="correct_memory",
                    content_hash=hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                ),
                *old.sources,
            ],
        )
        committed = self._write.commit_supersede(id, new, reason="manual correction")
        return CorrectResult(archived=id, new=committed)

    def update(self, id: str, **fields: object) -> Note:
        raise NotImplementedError

    def delete(self, id: str, *, force: bool = False) -> None:
        raise NotImplementedError

    def pin(self, id: str) -> Note:
        self._store.set_pinned(id, True)  # exempt from decay/archive (I13)
        return self.get(id)

    def forget(self, id: str) -> Note:
        self._store.archive(id, now=self._clock.now())  # archive-not-delete (I2), event co-written
        return self.get(id)

    def revive(self, id: str) -> Note:
        self._store.revive(id)  # un-archive: clears invalid_at/expired_at, event co-written
        return self.get(id)

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
        return self._store.get_notes(ids)

    def strength(self, id: str) -> Strength:
        return compute_strength(self.get(id), self._clock.now())

    def list_active(
        self,
        *,
        scope: Scope | None = None,
        sort: Literal["decay", "recent", "importance"] = "recent",
        limit: int = 200,
    ) -> list[Note]:
        """Active notes for the inspector/UI (the 'what I know about you now' list)."""
        return self._store.by_status(
            scope=scope or self._default_scope, status="active", sort=sort, limit=limit
        )

    def neighbors(
        self, id: str, *, relations: list[EdgeRelation] | None = None, hops: int = 1
    ) -> list[Edge]:
        return self._store.neighbors([id], relations=relations)  # 1-hop (multi-hop later)

    def fork_history(self, id: str) -> list[Note]:
        raise NotImplementedError

    # ── maintenance / forgetting ─────────────────────────────────────────
    def consolidate(
        self,
        *,
        scope: Scope | None = None,
        now: datetime | None = None,
        caps: dict[str, int] | None = None,
    ) -> ConsolidateResult:
        return self._consolidator.consolidate(
            scope=scope or self._default_scope, now=now, caps=caps
        )

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
        return self._procedural.optimize_prompt(name, trajectory, feedback)

    def get_procedural(self, name: str) -> str:
        return self._procedural.get_procedural(name)

    def set_procedural(self, name: str, text: str) -> Note:
        """Register/replace a behavior directive (procedural memory, SPEC §7)."""
        return self._procedural.set_procedural(name, text)
