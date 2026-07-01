"""LLM / Embedder / Clock seams (D10) — sacred ABCs + deterministic defaults."""

from __future__ import annotations

from cold_frame.llm.base import (
    LLM,
    Clock,
    Embedder,
    EmbedderMeta,
    HashEmbedder,
    LLMResult,
    SystemClock,
    TaskTag,
    Usage,
)

__all__ = [
    "LLM",
    "Clock",
    "Embedder",
    "EmbedderMeta",
    "HashEmbedder",
    "LLMResult",
    "SystemClock",
    "TaskTag",
    "Usage",
]
