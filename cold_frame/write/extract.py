"""EXTRACT — chat messages → candidate Note objects (write path, prompts.md §1).

Offline default (``llm=None``): naive 1-user-message = 1-fact (I5). With an LLM:
parse ``ExtractionOutput`` and apply the deterministic durability + confidence gates.
The LLM only proposes facts; code disposes — no freshness/archive decision here (I1).
Deterministic ids come from the injected ``new_id`` factory; time from ``clock`` (G6).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cold_frame.constants import CONFIDENCE_FLOOR
from cold_frame.llm.base import LLM, Clock, TaskTag
from cold_frame.models import Note, Scope, Source, TriageReason
from cold_frame.prompts import EXTRACT_SYSTEM
from cold_frame.prompts.extract import ExtractionOutput, build_user

if TYPE_CHECKING:
    from cold_frame.api import Msg

_NAIVE_CONFIDENCE = 0.5
_NAIVE_IMPORTANCE = 0.5
_DURABLE_MIN_CONF = 0.6
_DURABLE_MIN_IMPORTANCE = 0.5

# Deterministic tagging (offline-first, I5): coarse labels for grouping/filtering — the memory_type
# (a category) + a few salient content terms. Distinct from LLM `keywords` (search terms); derived
# the same way in both the naive and LLM paths so a tag is always present.
_TAG_TOKEN = re.compile(r"[a-z][a-z0-9]{3,}")  # lowercase word, length >= 4
_TAG_STOPWORDS: frozenset[str] = frozenset(
    [
        "this",
        "that",
        "with",
        "from",
        "have",
        "here",
        "there",
        "what",
        "when",
        "then",
        "they",
        "them",
        "your",
        "work",
        "about",
        "into",
        "over",
        "yours",
        "mine",
        "ours",
        "their",
        "been",
        "being",
        "will",
        "would",
        "should",
        "could",
        "prefer",
        "using",
        "used",
        "like",
        "want",
        "need",
        "make",
        "made",
        "does",
        "done",
        "onto",
        "every",
    ]
)
_TAG_MAX = 6  # memory_type + up to 5 salient terms


def derive_tags(content: str, memory_type: str) -> list[str]:
    """Coarse, deterministic tags: ``[memory_type]`` + salient content terms (lowercased, len>=4,
    stopword-filtered, dedup'd, capped). No LLM — works in the offline default (I5)."""
    tags: list[str] = [memory_type]
    seen = {memory_type}
    for tok in _TAG_TOKEN.findall(content.lower()):
        if tok not in _TAG_STOPWORDS and tok not in seen:
            tags.append(tok)
            seen.add(tok)
            if len(tags) >= _TAG_MAX:
                break
    return tags


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(messages: str | Sequence[Msg]) -> list[dict[str, str]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return [{"role": str(m["role"]), "content": str(m["content"])} for m in messages]


def _iso(dt: datetime) -> str:
    # fixed-width fractional seconds → sortable TEXT (see store._to_iso for why)
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_valid_at(s: str | None, default: datetime) -> datetime:
    if not s:
        return default
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return default


def extract(
    messages: str | Sequence[Msg],
    *,
    llm: LLM | None,
    clock: Clock,
    new_id: Callable[[], str],
    observed_at: datetime,
    scope: Scope,
    source: Source | None = None,
    infer: bool = True,
    raw: bool = False,
) -> list[Note]:
    """Turn input messages into candidate Notes (naive offline, or LLM-inferred)."""
    msgs = _normalize(messages)
    if llm is None or raw or not infer:
        return _naive(
            msgs, clock=clock, new_id=new_id, observed_at=observed_at, scope=scope, source=source
        )
    return _llm_extract(
        msgs,
        llm=llm,
        clock=clock,
        new_id=new_id,
        observed_at=observed_at,
        scope=scope,
        source=source,
    )


def _naive(
    msgs: list[dict[str, str]],
    *,
    clock: Clock,
    new_id: Callable[[], str],
    observed_at: datetime,
    scope: Scope,
    source: Source | None,
) -> list[Note]:
    notes: list[Note] = []
    for i, m in enumerate(msgs):
        if m["role"] != "user":  # naive: one fact per USER message (SPEC §4)
            continue
        content = m["content"]
        src = source or Source(
            kind="message",
            ref=f"msg:{i}",
            role=m["role"],
            content_hash=_sha256(content),
            observed_at=observed_at,
        )
        notes.append(
            Note(
                id=new_id(),
                content=content,
                memory_type="episodic",
                tags=derive_tags(content, "episodic"),
                confidence=_NAIVE_CONFIDENCE,
                importance=_NAIVE_IMPORTANCE,
                scope=scope,
                sources=[src],
                status="active",
                created_at=clock.now(),
                valid_at=observed_at,
                decay_S=1.0,
            )
        )
    return notes


def _llm_extract(
    msgs: list[dict[str, str]],
    *,
    llm: LLM,
    clock: Clock,
    new_id: Callable[[], str],
    observed_at: datetime,
    scope: Scope,
    source: Source | None,
) -> list[Note]:
    result = llm.complete(
        task=TaskTag.EXTRACT,
        system=EXTRACT_SYSTEM,
        user=build_user(
            json.dumps(msgs), observation_date=_iso(observed_at), current_date=_iso(clock.now())
        ),
        schema=ExtractionOutput,
    )
    parsed = result.parsed
    facts = parsed.facts if isinstance(parsed, ExtractionOutput) else []
    raw_hash = _sha256("\n".join(m["content"] for m in msgs))
    notes: list[Note] = []
    for f in facts:
        # durability gate (prompts §1.4): drop ephemeral chatter unless confident + important
        if f.durability == "ephemeral" and not (
            f.confidence >= _DURABLE_MIN_CONF and f.importance >= _DURABLE_MIN_IMPORTANCE
        ):
            continue
        # confidence gate → quarantine for human triage (I14; code uses 'low_confidence')
        held = f.confidence < CONFIDENCE_FLOOR
        triage: TriageReason | None = "low_confidence" if held else None
        src = source or Source(
            kind="message",
            ref="conversation",
            role=f.attributed_to,
            content_hash=raw_hash,
            observed_at=observed_at,
        )
        notes.append(
            Note(
                id=new_id(),
                content=f.text,
                memory_type=f.memory_type,
                keywords=f.keywords,
                tags=derive_tags(f.text, f.memory_type),
                context=f.context,
                confidence=f.confidence,
                importance=f.importance,
                scope=scope,
                sources=[src],
                status="active",
                created_at=clock.now(),
                valid_at=_parse_valid_at(f.valid_at, observed_at),
                held_for_human=held,
                quarantined=held,
                triage_reason=triage,
                decay_S=1.0,
            )
        )
    return notes
