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
from typing import Literal, TypedDict, cast, get_args

from cold_frame.branding import DB_PATH
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound, ToolError
from cold_frame.forget.consolidate import Consolidator
from cold_frame.llm.base import LLM, Clock, Embedder, HashEmbedder, SystemClock
from cold_frame.models import (
    AddResult,
    ConsolidateResult,
    CorrectResult,
    Edge,
    EdgeRelation,
    MemoryTypeLiteral,
    Note,
    ProceduralResult,
    Scope,
    SearchResult,
    Source,
    SourceKind,
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

_MEMORY_TYPES: frozenset[str] = frozenset(get_args(MemoryTypeLiteral))  # single source for the enum


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

    def _supersede_text(
        self,
        id: str,
        new_text: str,
        *,
        reason: str,
        ref: str,
        kind: SourceKind = "manual",
        scope: Scope | None = None,
    ) -> CorrectResult:
        """Replace ``id`` with a new fact carrying ``new_text`` via the one supersede commit.

        Keyed by an EXPLICIT id (not a similarity search) — correct_memory and the
        update_fact/supersede self-edit tools all funnel through here (I15).
        """
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
            valid_at=now,  # the replacement is true as of now
            importance=old.importance,
            sources=[
                Source(
                    kind=kind,
                    ref=ref,
                    content_hash=hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                ),
                *old.sources,
            ],
        )
        committed = self._write.commit_supersede(id, new, reason=reason)
        return CorrectResult(archived=id, new=committed)

    def correct_memory(
        self, id: str, new_text: str, *, scope: Scope | None = None
    ) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="manual correction", ref="correct_memory", scope=scope
        )

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
        """The self-edit tools an agent may call (api-contract §2.4). All converge on the
        one WriteCore (I15): create_fact→commit, update_fact/supersede→commit_supersede."""
        return [
            ToolSpec(
                name="create_fact",
                description="Assert a new fact (runs dedup; conflict resolution + freshness "
                "when an LLM is configured).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "memory_type": {"type": "string", "enum": sorted(_MEMORY_TYPES)},
                    },
                    "required": ["text"],
                },
            ),
            ToolSpec(
                name="update_fact",
                description="Correct the fact at id with new text; the old version is archived "
                "(revivable) and the new one supersedes it.",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            ),
            ToolSpec(
                name="supersede",
                description="Supersede the fact at id with a new fact (old archived, revivable).",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            ),
            ToolSpec(
                name="forget",
                description="Archive the fact at id (non-destructive, revivable).",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
        ]

    def create_fact(
        self,
        text: str,
        *,
        scope: Scope | None = None,
        memory_type: MemoryTypeLiteral = "semantic",
        importance: float = 0.5,
    ) -> AddResult:
        """Agent asserts a fact → the SAME WriteCore.commit as add (dedup + conflict, I15).

        ``confidence`` is intentionally left at the model default (1.0): an agent self-asserting
        a fact is high-confidence, distinct from passive extraction's 0.5 — so confidence/
        quarantine-gated cases are out of scope for the via_tool gate.
        """
        scope = scope or self._default_scope
        now = self._clock.now()
        cand = Note(
            id=self._new_id(),
            content=text,
            memory_type=memory_type,
            scope=scope,
            created_at=now,
            valid_at=now,
            importance=importance,
            sources=[
                Source(
                    kind="tool",
                    ref="create_fact",
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                )
            ],
        )
        return self._write.commit([cand], scope=scope, source=None)

    def update_fact(self, id: str, new_text: str, *, scope: Scope | None = None) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="agent update", ref="update_fact", kind="tool", scope=scope
        )

    def supersede(self, id: str, new_text: str, *, scope: Scope | None = None) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="agent supersede", ref="supersede", kind="tool", scope=scope
        )

    def apply_tool(
        self, name: str, args: dict[str, object], *, scope: Scope | None = None
    ) -> dict[str, object]:
        """Execute one self-edit tool by name (the MCP/agent entry); routes via WriteCore (I15).

        Every argument-boundary failure raises ``ToolError`` (a ColdFrameError) so the MCP
        layer maps it to a stable error code — never a bare KeyError/ValueError.
        """

        def _require(key: str) -> str:
            val = args.get(key)
            if not isinstance(val, str) or not val:
                raise ToolError(f"self-edit tool {name!r} requires a non-empty {key!r}")
            return val

        if name == "create_fact":
            mt = args.get("memory_type", "semantic")
            if mt not in _MEMORY_TYPES:
                raise ToolError(
                    f"invalid memory_type {mt!r} (expected one of {sorted(_MEMORY_TYPES)})"
                )
            res = self.create_fact(
                _require("text"), scope=scope, memory_type=cast(MemoryTypeLiteral, mt)
            )
            return {
                "added": [n.id for n in res.added],
                "deduped": res.deduped,
                "superseded": res.superseded,
                "held": [n.id for n in res.held],  # durability-gated, agent must see it
                "blocked": [b.reason for b in res.blocked],  # secret BLOCKed pre-disk (I6)
            }
        if name == "update_fact":
            r = self.update_fact(_require("id"), _require("text"), scope=scope)
            return {"archived": r.archived, "new": r.new.id}
        if name == "supersede":
            r = self.supersede(_require("id"), _require("text"), scope=scope)
            return {"archived": r.archived, "new": r.new.id}
        if name == "forget":
            note = self.forget(_require("id"))
            return {"archived": note.id, "status": note.status}
        raise ToolError(f"unknown self-edit tool {name!r}")

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        return self._procedural.optimize_prompt(name, trajectory, feedback)

    def get_procedural(self, name: str) -> str:
        return self._procedural.get_procedural(name)

    def set_procedural(self, name: str, text: str) -> Note:
        """Register/replace a behavior directive (procedural memory, SPEC §7)."""
        return self._procedural.set_procedural(name, text)
