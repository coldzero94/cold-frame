"""Prompt text constants (placeholder).

LLM prompt strings + JSON-schema descriptions live here (build/prompts.md). Real
content lands with each engine phase (EXTRACT in P1, CONFLICT in P2, etc.). Kept as
empty placeholders so the package imports cleanly in the scaffold.
"""

from __future__ import annotations

from typing import Final

from cold_frame.prompts.extract import EXTRACT_SYSTEM  # real EXTRACT prompt (P1)

# Placeholder prompt text — populated per phase (build/prompts.md). Empty for now.
ADMISSION_TIEBREAK_SYSTEM: Final[str] = ""
DEDUP_BATCH_SYSTEM: Final[str] = ""
CONFLICT_JUDGE_SYSTEM: Final[str] = ""
CONSOLIDATE_SUMMARY_SYSTEM: Final[str] = ""
RERANK_JUDGE_SYSTEM: Final[str] = ""
GRADIENT_DIAGNOSE_SYSTEM: Final[str] = ""
GRADIENT_EDIT_SYSTEM: Final[str] = ""

__all__ = [
    "ADMISSION_TIEBREAK_SYSTEM",
    "CONFLICT_JUDGE_SYSTEM",
    "CONSOLIDATE_SUMMARY_SYSTEM",
    "DEDUP_BATCH_SYSTEM",
    "EXTRACT_SYSTEM",
    "GRADIENT_DIAGNOSE_SYSTEM",
    "GRADIENT_EDIT_SYSTEM",
    "RERANK_JUDGE_SYSTEM",
]
