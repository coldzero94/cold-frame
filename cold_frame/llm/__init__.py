"""LLM / Embedder / Clock seams (D10) — sacred ABCs + deterministic defaults."""

from __future__ import annotations

from cold_frame.llm.base import (
    LLM,
    LOCAL_ONLY_TASKS,
    Clock,
    Embedder,
    EmbedderMeta,
    HashEmbedder,
    LLMResult,
    SystemClock,
    TaskTag,
    Usage,
    assert_local_for,
)

__all__ = [
    "LLM",
    "LOCAL_ONLY_TASKS",
    "Clock",
    "Embedder",
    "EmbedderMeta",
    "HashEmbedder",
    "LLMResult",
    "SystemClock",
    "TaskTag",
    "Usage",
    "assert_local_for",
]
