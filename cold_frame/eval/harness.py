"""Golden-set harness: Suite/Case models + YAML loader + a ``run()`` skeleton.

The loader is fully importable NOW (P1 scaffold); the actual assertion engine is
stubbed with ``NotImplementedError`` and filled in as phases land (eval §B.4).

Determinism (G6): each ``Case`` carries a fixed ``at`` instant and a ``seed``; the
runner builds a ``Memory`` with a ``FrozenClock`` + ``HashEmbedder`` + a
``ScriptedLLM`` driven by ``case.llm_script``. An LLM call for a ``TaskTag`` not in
the script is a hard ``EvalError`` — an undeclared LLM interaction never silently
passes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cold_frame.exceptions import ColdFrameError


class EvalError(ColdFrameError):
    """A golden case failed: an assertion mismatched, or an LLM call was unscripted."""


class Step(BaseModel):
    """One action in a case timeline (e.g. add / search / consolidate).

    ``at`` pins the FrozenClock for this step; ``action`` selects the Memory method;
    ``args`` are its keyword arguments; ``expect`` holds the declarative assertions.
    """

    at: datetime | None = None  # FrozenClock instant for this step (G6)
    action: str  # "add" | "search" | "consolidate" | "correct_memory" | ...
    args: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any] = Field(default_factory=dict)


class Case(BaseModel):
    """One golden test case (eval §B). Ids are ``uuid5(NS, f"{id}:{ordinal}")``-stable."""

    id: str
    description: str = ""
    seed: int = 0  # seeds all tiebreak RNG (deterministic)
    # llm_script: TaskTag value -> ordered list of scripted responses (declared up front)
    llm_script: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    steps: list[Step] = Field(default_factory=list)


class Suite(BaseModel):
    """A named collection of cases loaded from one YAML file (eval §B.4 gate mapping)."""

    name: str
    description: str = ""
    cases: list[Case] = Field(default_factory=list)


class CaseReport(BaseModel):
    """Per-case outcome in a run."""

    case_id: str
    passed: bool
    error: str | None = None


class RunReport(BaseModel):
    """Aggregate result of running a Suite (eval §B.4)."""

    suite: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    cases: list[CaseReport] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def load_suite(path: str | Path) -> Suite:
    """Load + validate a golden-set Suite from a YAML file. Importable in P1.

    Raises ``EvalError`` if the file is missing or not a mapping; pydantic raises
    ``ValidationError`` on a schema mismatch.
    """
    p = Path(path)
    if not p.is_file():
        raise EvalError(f"suite file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvalError(f"suite YAML must be a mapping, got {type(data).__name__}: {p}")
    return Suite.model_validate(data)


def run(suite: Suite, *, llm: object | None = None) -> RunReport:
    """Execute every case against a deterministic Memory and assert ``expect`` blocks.

    Skeleton only (P1): wiring (FrozenClock + HashEmbedder + ScriptedLLM from
    ``case.llm_script``, ``case.seed`` RNG, ``Memory`` per case) and the assertion
    engine land with the phases they gate. An unmatched LLM call MUST surface as an
    ``EvalError`` here, never a silent pass.
    """
    raise NotImplementedError(
        "eval.run assertion engine lands with P1+ phases (see eval §B.4); "
        "load_suite() is usable now."
    )
