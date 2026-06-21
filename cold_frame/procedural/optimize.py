"""ProceduralOptimizer — gradient diagnose → edit, with f-string var-healer (SPEC §7).

Leaf stub. Bodies raise ``NotImplementedError``; P5 fills them in (GRADIENT_DIAGNOSE
gate → GRADIENT_EDIT; preserve all f-string vars or raise ``VarHealerError``).
"""

from __future__ import annotations

from cold_frame.api import Msg
from cold_frame.llm.base import LLM, Clock
from cold_frame.models import ProceduralResult
from cold_frame.store.base import Store


class ProceduralOptimizer:
    """Self-improving behavior directives via reflective gradient edits (D9)."""

    def __init__(self, store: Store, *, llm: LLM | None, clock: Clock) -> None:
        self._store = store
        self._llm = llm
        self._clock = clock

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        raise NotImplementedError

    def get_procedural(self, name: str) -> str:
        """Current behavior directive for ``name``; ``""`` if none."""
        raise NotImplementedError
