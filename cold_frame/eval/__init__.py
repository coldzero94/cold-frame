"""Eval harness — the integration backbone (CLAUDE.md §2, eval §B).

Engine correctness is proven by deterministic mock-LLM golden cases (Suite/Case
YAML), not hand-poking. Every LLM interaction is declared in ``llm_script``; an
unmatched call is a hard ``EvalError`` by design.
"""

from __future__ import annotations

from cold_frame.eval.harness import (
    Case,
    CaseReport,
    EvalError,
    ScriptedLLM,
    Suite,
    SuiteReport,
    load_suite,
    run_case,
    run_suite,
)

__all__ = [
    "Case",
    "CaseReport",
    "EvalError",
    "ScriptedLLM",
    "Suite",
    "SuiteReport",
    "load_suite",
    "run_case",
    "run_suite",
]
