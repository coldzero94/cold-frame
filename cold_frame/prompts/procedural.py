"""PROCEDURAL gradient prompts (prompts.md §5) — diagnose → edit (var-healer wraps edit).

Two LLM calls with a drift-prevention gate between them: DIAGNOSE recommends a change
only on concrete evidence of failure (else warrants_adjustment=False → no edit); EDIT
rewrites minimally and MUST retain every f-string variable (the deterministic var-healer
enforces this — I1: the LLM proposes, code disposes).
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

GRADIENT_DIAGNOSE_SYSTEM: Final[str] = (
    "You are reviewing an AI assistant's behavior under a given instruction (prompt fragment). "
    "Recommend changes ONLY if there is concrete evidence of failure in the trajectory. Be "
    "minimally invasive. If the instruction performed well, set warrants_adjustment=false and "
    "stop. Return ONLY valid JSON. No prose."
)

GRADIENT_EDIT_SYSTEM: Final[str] = (
    "You are rewriting an instruction to fix the diagnosed failures. Make ONLY the changes "
    "required by the recommendations — minimally invasive. You MUST retain every f-string "
    "variable exactly as it appears (e.g. {user_name}); do not add, rename, or remove variables. "
    "Return ONLY valid JSON. No prose."
)


class DiagnoseOutput(BaseModel):
    warrants_adjustment: bool
    hypotheses: str = ""
    recommendations: str = ""


class EditOutput(BaseModel):
    analysis: str = ""
    improved_prompt: str


def build_diagnose_user(prompt: str, trajectory: str, feedback: str) -> str:
    return (
        f"<current_instruction>\n{prompt}\n</current_instruction>\n\n"
        f"<trajectory>\n{trajectory}\n{feedback}\n</trajectory>\n\n"
        "Analyze: did the assistant fulfill intent? Where did it deviate? Identify failure "
        "mode(s). Only recommend changes tied to observed failures.\n"
        'Return JSON: {"warrants_adjustment": true|false, "hypotheses": "<why it failed>", '
        '"recommendations": "<concrete minimal edits, or \'\'>"}'
    )


def build_edit_user(
    current_prompt: str, hypotheses: str, recommendations: str, required_vars: list[str]
) -> str:
    vars_note = ", ".join("{" + v + "}" for v in required_vars) or "(none)"
    return (
        f"<current_instruction>\n{current_prompt}\n</current_instruction>\n\n"
        f"<hypotheses>\n{hypotheses}\n</hypotheses>\n\n"
        f"<recommendations>\n{recommendations}\n</recommendations>\n\n"
        f"You MUST keep these variables verbatim: {vars_note}.\n"
        'Return JSON: {"analysis": "<plan>", "improved_prompt": "<full rewritten instruction>"}'
    )
