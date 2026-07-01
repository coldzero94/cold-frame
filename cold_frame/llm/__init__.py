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


def resolve_llm(name: str | None) -> LLM | None:
    """Map a ``$COLD_FRAME_LLM`` / config name to an ``LLM`` (or ``None`` = offline default).

    Unset / ``"none"`` → ``None``: the offline default runs deterministic DEDUP (cosine) + freshness
    on EXPLICIT corrections, but NO automatic contradiction detection (that ``_classify`` step is
    LLM-gated — it needs a model to propose "contradiction" before freshness can supersede).
    ``"claude"`` → ``ClaudeCliLLM`` (the user's Claude Code session, no API key) enables extraction
    + the dedup/conflict judges, so automatic conflict detection works. REMOTE (``is_local=False``):
    the candidate + its nearest note are sent to the model (the ``write/extract`` egress guard still
    blocks obvious secrets). The ``worker`` already auto-uses ``ClaudeCliLLM`` when ``claude`` is on
    PATH; this env turns the same on for the CLI ``add`` / MCP ``add_memory`` paths.
    """
    from cold_frame.exceptions import ColdFrameError

    key = (name or "none").strip().lower()
    if key in ("", "none", "off", "offline"):
        return None
    if key in ("claude", "claude-cli", "cli"):
        from cold_frame.llm.claude_cli import ClaudeCliLLM

        if not ClaudeCliLLM.available():
            raise ColdFrameError(
                "COLD_FRAME_LLM=claude but the `claude` CLI is not on PATH — install Claude Code, "
                "or unset COLD_FRAME_LLM for the offline default"
            )
        return ClaudeCliLLM()
    raise ColdFrameError(
        f"unknown COLD_FRAME_LLM {name!r} — use 'claude' or unset (offline default)"
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
    "resolve_embedder",
    "resolve_llm",
]
