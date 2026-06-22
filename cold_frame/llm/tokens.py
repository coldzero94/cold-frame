"""Offline token counting (read-and-budget §5.10).

The default ``HeuristicCounter`` is dep-free (chars/4 with a small word blend) so the
budget packer works fully offline/keyless (I5). ``tiktoken`` (via the ``[openai]`` extra)
is an optional exact counter; selection falls back gracefully when it is not installed —
the harness and packer MUST use the same counter, else a budget assertion is meaningless.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenCounter(Protocol):
    """A pluggable token counter (count + budget-aware truncate)."""

    name: str

    def count(self, text: str) -> int: ...

    def truncate(self, text: str, max_tokens: int) -> str: ...


class HeuristicCounter:
    """Dep-free default: chars/4 blended with a word count (CJK/code-robust)."""

    name = "heuristic-chars4"

    def count(self, text: str) -> int:
        if not text:
            return 0
        chars = len(text)
        words = len(text.split())
        return max(1, round(0.75 * (chars / 4) + 0.25 * words))

    def truncate(self, text: str, max_tokens: int) -> str:
        return text[: max(0, max_tokens) * 4]  # inverse of chars/4; caller adds any ellipsis


class _TiktokenCounter:
    """Exact GPT-family counts via tiktoken cl100k_base (optional, ``[openai]`` extra)."""

    name = "tiktoken:cl100k_base"

    def __init__(self) -> None:
        import tiktoken

        self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def truncate(self, text: str, max_tokens: int) -> str:
        out: str = self._enc.decode(self._enc.encode(text)[: max(0, max_tokens)])
        return out


def get_token_counter(name: str = "heuristic") -> TokenCounter:
    """Resolve the active counter; ``tiktoken`` falls back to heuristic when absent."""
    if name == "tiktoken":
        try:
            return _TiktokenCounter()
        except ImportError:
            pass  # graceful offline fallback (never crash without the extra)
    return HeuristicCounter()
