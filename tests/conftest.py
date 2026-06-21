"""Shared test fixtures (eval §B.2 — determinism: FrozenClock + ScriptedLLM).

These bake the G6 seams into every test:
- ``FrozenClock`` — a fixed-instant ``Clock`` so no test touches wall time.
- ``ScriptedLLM`` — a ``FakeLLM`` returning responses keyed by ``TaskTag``; an
  unmatched call is a hard error by design (eval §B: an undeclared LLM call is
  an ``EvalError``).
- ``db_path`` — a throwaway SQLite path under ``tmp_path``.
- ``memory`` — a ``Memory`` wired with ``HashEmbedder`` + ``llm=None`` (the offline
  default, I5) + the ``FrozenClock``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.llm.base import LLM, LLMResult, TaskTag
from cold_frame.models import Scope
from pydantic import BaseModel

# A fixed, tz-aware UTC instant every test shares (stable snapshots / ids).
FROZEN_INSTANT = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


class FrozenClock:
    """A ``Clock`` pinned to a single instant (G6). Satisfies the ``Clock`` protocol."""

    def __init__(self, instant: datetime = FROZEN_INSTANT) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


class ScriptedLLM(LLM):
    """A ``FakeLLM`` that replays scripted results keyed by ``TaskTag`` (eval §A).

    Construct with a mapping ``{TaskTag: LLMResult}``. A ``complete`` call for a
    task not in the script raises ``AssertionError`` — an undeclared LLM
    interaction is a hard failure by design, never a silent fallback.
    """

    name = "mock"

    def __init__(self, script: dict[TaskTag, LLMResult] | None = None, *, is_local: bool = True):
        self._script: dict[TaskTag, LLMResult] = dict(script or {})
        self._is_local = is_local
        self.calls: list[TaskTag] = []

    @property
    def is_local(self) -> bool:
        return self._is_local

    def complete(
        self,
        *,
        task: TaskTag,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResult:
        self.calls.append(task)
        if task not in self._script:
            raise AssertionError(
                f"ScriptedLLM: unscripted LLM call for task={task.value!r} "
                f"(declare it in the script — an undeclared call is an error by design)"
            )
        return self._script[task]


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A deterministic clock pinned to ``FROZEN_INSTANT``."""
    return FrozenClock()


@pytest.fixture
def scripted_llm() -> ScriptedLLM:
    """An empty ScriptedLLM (add to ``._script`` per-test). Raises on any unscripted call."""
    return ScriptedLLM()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """A throwaway SQLite DB path (never the user's real ~/.cold-frame/memory.db)."""
    return str(tmp_path / "memory.db")


@pytest.fixture
def memory(db_path: str, frozen_clock: FrozenClock) -> Memory:
    """A Memory wired offline: HashEmbedder (default) + llm=None + FrozenClock (I5/G6).

    ``embedder=None`` lets ``Memory`` pick its default HashEmbedder; ``llm=None``
    is the naive-extract offline path. The clock is injected for determinism.
    """
    from cold_frame.llm.base import HashEmbedder

    return Memory(
        db_path,
        embedder=HashEmbedder(),
        llm=None,
        default_scope=Scope(),
        clock=frozen_clock,
    )
