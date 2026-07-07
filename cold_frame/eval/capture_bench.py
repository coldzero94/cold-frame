"""Capture-quality benchmark — the anti-bloat proof for auto-capture (D26).

The product bet is that Claude Code sessions auto-capture the durable facts WITHOUT hoarding the
noise. This measures that on a labeled transcript: of the user turns, the Layer-A salience filter
(``read_user_messages``) should KEEP the durable first-person facts/decisions and DROP the noise
(questions, task requests, slash/bash, trivially short turns, oversized pastes) — vs a
capture-everything baseline (the mem0-shaped 'store every message' strategy) that recalls everything
but at a precision equal to the durable fraction (it hoards the noise).

Fully deterministic + offline (no LLM, no network): the metric is the Layer-A decision on a fixed,
hand-labeled transcript. Reproducible — run ``python -m cold_frame.eval.capture_bench``. The claims
are pinned in ``tests/test_capture_bench.py`` so a salience regression fails CI.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cold_frame.integrations.claude_code import read_user_messages

# (message text, should_capture). A realistic Claude Code session: durable user facts/decisions
# interleaved with the noise a coding session is mostly made of. Only USER turns are labeled — the
# salience decision Layer-A makes (assistant/tool turns are excluded by type, tested apart).
LABELED: list[tuple[str, bool]] = [
    # durable — the facts worth remembering across sessions
    ("I prefer dark roast coffee in the morning", True),
    ("I deploy this repo with ship.sh every night", True),
    ("I switched my main language to Rust from Go last month", True),
    ("we decided to use Postgres for the analytics service", True),
    ("I am allergic to peanuts and shellfish", True),
    ("my timezone is Asia/Seoul and I work best late at night", True),
    ("the production database is hosted on the us-east-1 region", True),
    # noise — what a session is mostly made of; must NOT become memories
    ("how do I run the tests?", False),
    ("run the tests and fix any failures", False),
    ("can you refactor this file to be cleaner?", False),
    ("what does this function do?", False),
    ("ok thanks", False),
    ("yes go ahead", False),
    ("/clear", False),
    ("git commit -m 'wip'", False),
    ("explain the difference between these two approaches", False),
]


def _write_transcript(path: Path) -> None:
    lines = [
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        )
        for text, _ in LABELED
    ]
    # a couple of non-user turns (assistant / tool_result) — the pipeline must ignore these by type
    lines.append(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "I prefer tabs over spaces"}],
                },
            }
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pr(captured: set[str], should: set[str]) -> tuple[float, float]:
    if not captured:
        return 0.0, 0.0
    hit = len(captured & should)
    return hit / len(captured), hit / len(should)  # precision, recall


def evaluate() -> dict[str, dict[str, float]]:
    """Layer-A salience vs a capture-everything baseline on the labeled transcript."""
    should = {text for text, keep in LABELED if keep}
    all_user = {text for text, _ in LABELED}

    tpath = Path(tempfile.mkdtemp()) / "transcript.jsonl"
    _write_transcript(tpath)
    kept = {m["content"] for m in read_user_messages(tpath)[0]}  # Layer-A survivors

    cf_p, cf_r = _pr(kept, should)
    base_p, base_r = _pr(all_user, should)  # capture-everything: keeps every user turn
    return {
        "coldframe": {"precision": cf_p, "recall": cf_r, "captured": float(len(kept))},
        "capture_everything": {
            "precision": base_p,
            "recall": base_r,
            "captured": float(len(all_user)),
        },
        "meta": {"durable": float(len(should)), "user_turns": float(len(all_user))},
    }


def format_report(r: dict[str, dict[str, float]]) -> str:
    cf, be, meta = r["coldframe"], r["capture_everything"], r["meta"]

    def row(name: str, d: dict[str, float]) -> str:
        return f"  {name:<20} {d['precision']:>9.0%} {d['recall']:>7.0%} {int(d['captured']):>9}"

    return "\n".join(
        [
            f"Capture-quality benchmark — {int(meta['durable'])} durable facts in "
            f"{int(meta['user_turns'])} user turns",
            f"  {'strategy':<20} {'precision':>9} {'recall':>7} {'captured':>9}",
            row("Layer-A salience", cf),
            row("capture-everything", be),
            "",
            "  → Layer-A keeps the durable facts while dropping the session noise (high precision,",
            "    no hoarding); capture-everything recalls all of them but buries them in noise",
            "    (precision = the durable fraction). Fully offline + deterministic — no LLM.",
        ]
    )


def main() -> None:  # pragma: no cover - human-facing report
    print(format_report(evaluate()))


if __name__ == "__main__":  # pragma: no cover
    main()
