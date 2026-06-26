"""Tier classification prompt (D26 project scoping).

Is an auto-captured user statement a GLOBAL fact (about the user as a person — recalled in every
project) or a PROJECT fact (about this codebase/task)? The LLM proposes; ``is_global_fact`` (the
deterministic heuristic) is the offline / malformed-reply fallback. The host model reaches this via
MCP sampling inside the capture drain — the same parasitic LLM the dedup/conflict judges use.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

SCOPE_SYSTEM: Final[str] = (
    "Classify each user statement as 'global' or 'project'. "
    "GLOBAL = a durable fact about the USER as a person that holds across ALL their projects "
    "(identity, preferences, habits, tools they always use, contact info). "
    "PROJECT = a fact specific to the CURRENT codebase/repo/task (its stack, conventions, deploy "
    "steps, decisions made in this project). "
    "When unsure, choose 'project' — it is the safer default (it will not leak across repos). "
    'Return JSON {"tiers": ["global"|"project", ...]} — exactly one label per statement, in order.'
)


class ScopeVerdict(BaseModel):
    """One tier label per input statement, in order (global = cross-project)."""

    tiers: list[Literal["global", "project"]]


def build_scope_user(texts: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    return f"Statements:\n{numbered}\n\nReturn one label per statement, in the same order."
