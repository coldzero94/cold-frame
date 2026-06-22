"""Meta boost tests (P3 unit 4): recency/scope nudge, clamped to +15%, deterministic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cold_frame.models import Note, Scope, SearchHit, Signals, Source
from cold_frame.read.rerank import apply_meta_boost

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _hit(
    nid: str, score: float, *, last_accessed: datetime, session: str | None = None
) -> SearchHit:
    note = Note(
        id=nid,
        content=f"fact {nid}",
        memory_type="semantic",
        scope=Scope(session_id=session),
        created_at=last_accessed,
        last_accessed=last_accessed,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=last_accessed)],
    )
    return SearchHit(note=note, score=score, signals=Signals(rrf=score))


def test_recent_note_is_promoted_over_equal_rrf() -> None:
    recent = _hit("recent", 0.50, last_accessed=NOW)
    old = _hit("old", 0.50, last_accessed=NOW - timedelta(days=365))
    out = apply_meta_boost([old, recent], now=NOW, scope=Scope())
    assert out[0].note.id == "recent"  # equal RRF → recency breaks toward the recent note


def test_boost_is_clamped_to_15_percent() -> None:
    h = _hit("h", 1.0, last_accessed=NOW, session="s1")
    apply_meta_boost([h], now=NOW, scope=Scope(session_id="s1"))  # recency + scope match
    assert h.score <= 1.0 * 1.15 + 1e-9  # never lifted by more than +15%


def test_scope_session_match_adds_weight() -> None:
    match = _hit("m", 0.50, last_accessed=NOW, session="s1")
    nomatch = _hit("n", 0.50, last_accessed=NOW, session="s2")
    out = apply_meta_boost([nomatch, match], now=NOW, scope=Scope(session_id="s1"))
    assert out[0].note.id == "m"  # the session-matching note ranks first
