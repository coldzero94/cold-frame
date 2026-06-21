"""``Memory`` facade (api-contract §2) — the single public Python entrypoint.

This is the canonical SIGNATURE surface (G6 bakes the Clock/id-factory into __init__).
Method bodies are scaffold stubs raising ``NotImplementedError``; downstream phases
(P1+) fill them in WITHOUT changing these signatures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from cold_frame.llm.base import LLM, Clock, Embedder, SystemClock
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
        config: object | None = None,
    ) -> None:
        # On init (P1): open Store, run Store.migrate() (idempotent), assert embedder dim
        # matches DB meta else raise EmbedderMismatchError. Clock/id-factory injected (G6).
        self._db_path = db_path
        self._embedder = embedder
        self._llm = llm
        self._default_scope = default_scope or Scope()
        self._clock: Clock = clock or SystemClock()
        self._config = config

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
        raise NotImplementedError

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
        raise NotImplementedError

    def get(self, id: str) -> Note:
        raise NotImplementedError

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
