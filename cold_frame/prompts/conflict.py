"""DEDUP + CONFLICT prompts (prompts.md §3). The LLM only proposes; code disposes (I1).

Both steps judge a single (NEW, EXISTING) pair and return a ``ConflictVerdict``
(models.py) — the code-canonical single-pair shape (the doc's batch-idx form is
superseded). Deterministic freshness (valid_at comparison) is NEVER done by the LLM.
"""

from __future__ import annotations

from typing import Final

DEDUP_SYSTEM: Final[str] = (
    "You are a fact deduplication assistant for a personal memory store. Decide ONLY whether a "
    "NEW fact states the SAME thing as an EXISTING fact at the SAME specificity. NEVER mark facts "
    "with key differences (numbers, dates, qualifiers, proper nouns) as duplicates. "
    "Return ONLY valid JSON. No prose."
)

CONFLICT_SYSTEM: Final[str] = (
    "You are a fact conflict-resolution assistant for a personal memory store. You decide ONLY "
    "whether a NEW fact duplicates or contradicts a known fact. You do NOT decide which is newer "
    "or what to archive — deterministic code does that using timestamps. Never mark facts with "
    "key differences (numbers, dates, qualifiers) as duplicates. Return ONLY valid JSON. No prose."
)


def build_dedup_user(new: str, existing: str) -> str:
    return (
        f"NEW: {new}\n"
        f"EXISTING: {existing}\n\n"
        "Same meaning, different wording, no new specifics → relation=duplicate. "
        "Any added/changed number, date, name, or qualifier → relation=unrelated.\n"
        'Return JSON: {"relation": "duplicate"|"unrelated", "confidence": 0.0-1.0, '
        '"rationale": "<=12 words"}'
    )


def build_conflict_user(new: str, new_valid: str, existing: str, existing_valid: str) -> str:
    return (
        f"NEW FACT: {new} (valid_at {new_valid})\n"
        f"EXISTING FACT: {existing} (valid_at {existing_valid})\n\n"
        "relation=duplicate if identical content; relation=contradiction if same subject+relation "
        "but an incompatible value (e.g. 'works at X' vs 'works at Y'); relation=unrelated if a "
        "different topic or different events on different days. Do NOT judge which is newer.\n"
        'Return JSON: {"relation": "duplicate"|"contradiction"|"unrelated", "confidence": 0.0-1.0, '
        '"rationale": "<=12 words"}'
    )
