"""EXTRACTION prompt text + output schema (prompts.md §1).

The system prompt and the pydantic schema the LLM must return. The deterministic
post-processing (durability/confidence gates, field map → Note) lives in
``write/extract.py`` — the LLM only proposes facts; code disposes (I1).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field

# Bumped on any prompt-text change; stamped into sources[].extractor (R6 provenance).
PROMPT_VERSION: Final[str] = "extract/v1"

EXTRACT_SYSTEM: Final[str] = (
    "You are Coldframe's Memory Extractor — a precise, evidence-bound processor. Your only\n"
    "operation is to extract self-contained, atomic, contextually-grounded factual statements\n"
    "about the user (and named speakers) from a conversation. You do not delete, merge, or judge\n"
    "freshness — downstream deterministic code does that. You extract.\n\n"
    "Each extracted fact must be ONE atomic, self-contained statement (one subject-predicate\n"
    "idea), 15-80 words, with every pronoun and relative-time reference resolved. Split compound\n"
    "sentences into separate facts. Never generalize concrete details. Classify each fact's type\n"
    "and assign a confidence and a durability class.\n\n"
    "Return ONLY valid JSON parsable by json.loads(). No prose. No markdown fences."
)


class ExtractedFact(BaseModel):
    """One atomic fact proposed by the extraction LLM (prompts.md §1.3)."""

    text: str
    memory_type: Literal["semantic", "episodic", "procedural"]
    keywords: list[str] = Field(default_factory=list)
    context: str = ""
    valid_at: str | None = None  # ISO-8601 UTC; resolved against observation_date
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0, default=0.5)
    durability: Literal["durable", "ephemeral"]
    attributed_to: str = "user"
    linked_ids: list[int] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    """The whole structured-output object the LLM returns for one ``add()``."""

    facts: list[ExtractedFact] = Field(default_factory=list)


def build_user(messages_json: str, *, observation_date: str, current_date: str) -> str:
    """The extraction user prompt (prompts.md §1.2), minimal P1 shape."""
    return (
        f"## New Messages\n{messages_json}\n\n"
        f"## Observation Date (the ONLY anchor for relative time)\n{observation_date}\n\n"
        f"## Current Date (today; NEVER use to resolve relative references)\n{current_date}\n\n"
        "Extract atomic, self-contained facts about the user per the rules above. "
        'Return ONLY JSON: {"facts": [ {text, memory_type, keywords, context, valid_at, '
        "confidence, importance, durability, attributed_to, linked_ids}, ... ]}."
    )
