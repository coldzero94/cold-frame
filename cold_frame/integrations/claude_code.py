"""Claude Code transcript reader + Layer-A salience pre-filter (auto-capture, D26).

The hook enqueues a transcript POINTER; the drain calls this to read only the NEW user-message text
since the watermark. Layer-A is the cheap, deterministic, zero-LLM front line of the anti-bloat
design: keep the user's stated facts/decisions/corrections, drop assistant output, tool_use/
tool_result noise, and trivially short turns — so the LLM extractor + durability gate downstream
only ever see a small, salient slice. Stdlib only (json + pathlib); no heavy deps (I9).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cold_frame.api import Msg  # type-only — a runtime import would cycle

_MIN_CHARS = 12  # drop trivially short user turns ("ok", "yes", "thanks", "go on")
_MAX_CHARS = (
    4000  # skip pasted blobs (logs/files/stack traces) — almost never a stated durable fact
)

# Leading words that mark a turn as a task-REQUEST (imperative), not a durable fact about the user.
# Deterministic durability heuristic for the offline path, which (unlike the LLM extractor) has no
# durability gate — without this, "run the tests again" gets stored as a permanent "user fact".
_COMMAND_VERBS = frozenset(
    {
        "run", "show", "fix", "check", "look", "give", "tell", "explain", "find", "open",
        "write", "create", "make", "add", "list", "search", "read", "edit", "remove", "update",
        "change", "refactor", "implement", "generate", "test", "build", "commit", "push",
        "install", "rename", "move", "delete", "print", "help", "review", "try", "use",
    }
)
_REQUEST_PREFIXES = (
    "can you",
    "could you",
    "would you",
    "please ",
    "let's ",
    "lets ",
    "how do",
    "what",
)


def _is_durable_user_fact(text: str) -> bool:
    """Layer-A salience: keep declarative first-person-ish statements; drop questions, imperatives/
    task-requests, trivially short turns, and oversized pastes. Heuristic + deterministic."""
    if not (_MIN_CHARS <= len(text) <= _MAX_CHARS):
        return False
    t = text.strip().lower()
    if t.endswith("?") or t.startswith(_REQUEST_PREFIXES):
        return False
    first = t.split(maxsplit=1)[0] if t else ""
    return first not in _COMMAND_VERBS


def _user_text(message: object) -> str:
    """Plain text of a user message, skipping tool_result blocks (which arrive under role=user)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def read_user_messages(transcript_path: str | Path, since_line: int = 0) -> tuple[list[Msg], int]:
    """Read NEW user-message text from a Claude Code transcript JSONL after line ``since_line``.

    Returns ``(messages, new_line_count)``; the count is the watermark for the next read so capture
    is incremental + idempotent. CRITICAL: if the file is now SHORTER than the watermark (Claude
    Code compacted/rotated the transcript), reset to a full re-scan — else every post-compaction
    fact is silently lost forever. DEDUP + Layer-B collapse any carry-over, so a re-scan is safe.
    Layer-A keeps only durable user-stated facts (see _is_durable_user_fact); never raises.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return [], since_line
    lines = p.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = 0 if total < since_line else since_line  # shrink/rotation → re-scan the whole file
    msgs: list[Msg] = []
    for raw in lines[start:]:
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue  # a partial/corrupt line — skip, keep scanning
        if not isinstance(ev, dict) or ev.get("type") != "user":
            continue  # assistant / tool_result / metadata lines are not user-stated facts
        text = _user_text(ev.get("message"))
        if _is_durable_user_fact(text):
            msgs.append({"role": "user", "content": text})
    return msgs, total
