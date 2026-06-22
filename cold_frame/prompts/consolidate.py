"""CONSOLIDATE_SUMMARY prompt + schema (prompts.md §4) — episodic cluster → semantic.

The LLM only synthesizes a dense summary of a same-topic episodic cluster; deterministic
code creates the semantic note + derived_from edges and cold-demotes the originals (I1:
the LLM proposes, code disposes; non-destructive — originals are not deleted).
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field

CONSOLIDATE_SUMMARY_SYSTEM: Final[str] = (
    "You combine several related episodic memories into ONE dense, semantic summary fact. "
    "Preserve every materially relevant name, role, place, date, count, and change-over-time "
    "explicitly supported by the inputs. Prefer compact factual sentences. Do not invent. Do not "
    "include procedural instructions. Return ONLY valid JSON. No prose."
)


class ConsolidationOutput(BaseModel):
    """The semantic summary the LLM returns for one episodic cluster (prompts.md §4)."""

    summary: str
    keywords: list[str] = Field(default_factory=list)
    valid_at: str | None = None  # ISO-8601 UTC of the earliest supported fact
    source_idx: list[int] = Field(default_factory=list)


def build_consolidate_user(cluster: list[dict[str, str]]) -> str:
    """``cluster`` = ordered [{idx, text, valid_at}] (contiguous int idx, UUIDs never sent)."""
    lines = "\n".join(
        f'  {{"idx": {i}, "text": {m["text"]!r}, "valid_at": {m.get("valid_at", "")!r}}}'
        for i, m in enumerate(cluster)
    )
    return (
        "These EPISODIC memories are about the same topic. Produce ONE semantic summary "
        "capturing the durable takeaway across them (the standing fact/preference/pattern). "
        "Keep all explicitly-supported proper nouns, numbers, and dated changes. <= 80 words.\n\n"
        f"<EPISODIC MEMORIES>\n{lines}\n</EPISODIC MEMORIES>\n\n"
        'Return JSON: {"summary": "<semantic fact>", "keywords": ["..."], '
        '"valid_at": "<ISO-8601 of earliest supported>", "source_idx": [0,1,...]}'
    )
