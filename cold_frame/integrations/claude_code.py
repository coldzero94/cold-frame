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


def read_user_messages(
    transcript_path: str | Path, since_line: int = 0
) -> tuple[list[Msg], int]:
    """Read NEW user-message text from a Claude Code transcript JSONL after line ``since_line``.

    Returns ``(messages, new_line_count)``; the count is the watermark for the next read so capture
    is incremental + idempotent on an append-only transcript. Only user-role text survives Layer-A
    (assistant turns, tool noise, and sub-``_MIN_CHARS`` turns are dropped); the extractor's
    durability gate + dedup do the rest. Never raises — a malformed line is skipped.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return [], since_line
    msgs: list[Msg] = []
    line_no = since_line
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if line_no <= since_line or not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue  # a partial/corrupt line — skip, keep scanning
        if not isinstance(ev, dict) or ev.get("type") != "user":
            continue  # assistant / tool_result / metadata lines are not user-stated facts
        text = _user_text(ev.get("message"))
        if text and len(text) >= _MIN_CHARS:
            msgs.append({"role": "user", "content": text})
    return msgs, max(line_no, since_line)
