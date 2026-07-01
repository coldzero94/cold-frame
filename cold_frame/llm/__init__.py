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


def resolve_embedder(name: str | None) -> Embedder:
    """Map a ``$COLD_FRAME_EMBEDDER`` / config name to an ``Embedder``.

    Unset / ``"hash"`` → the offline ``HashEmbedder`` (I5 default: zero deps, no download, lexical).
    ``"local"`` / ``"bge-small"`` → the ``[local-llm]`` ``SentenceTransformerEmbedder`` (real
    semantic recall; needs the extra + a one-time model download). NOTE: switching embedders on an
    existing DB leaves the old vectors STALE (KNN filters on embedder_id, I10) until
    ``cold-frame reembed`` re-indexes them — ``doctor`` reports the stale count.
    """
    from cold_frame.exceptions import ColdFrameError

    key = (name or "hash").strip().lower()
    if key in ("", "hash", "default"):
        return HashEmbedder()
    if key in ("local", "bge", "bge-small", "st", "sentence-transformers"):
        try:
            from cold_frame.llm.local import SentenceTransformerEmbedder

            return SentenceTransformerEmbedder()
        except ImportError as exc:  # [local-llm] not installed → clean, actionable error
            raise ColdFrameError(str(exc)) from exc
    raise ColdFrameError(f"unknown COLD_FRAME_EMBEDDER {name!r} — use 'hash' (default) or 'local'")


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
    "resolve_embedder",
]
