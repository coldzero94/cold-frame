"""Strength/band tests (P3 unit 5a): display S + growth band + at-risk + list_active."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cold_frame.api import Memory
from cold_frame.models import Note, Scope, Source
from cold_frame.read.strength import compute_strength

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _note(
    content: str,
    *,
    importance: float = 0.5,
    confidence: float = 1.0,
    last_accessed: datetime | None = None,
    created: datetime = NOW,
) -> Note:
    return Note(
        id="n",
        content=content,
        memory_type="semantic",
        scope=Scope(),
        created_at=created,
        importance=importance,
        confidence=confidence,
        last_accessed=last_accessed,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=created)],
    )


def test_fresh_high_importance_is_evergreen() -> None:
    s = compute_strength(_note("x", importance=1.0), NOW)
    # retrievability 1.0 + importance 1.0 → 0.45 + 0.35 = 0.80 >= 0.66
    assert s.band == "evergreen"
    assert s.value >= 0.66
    assert s.at_risk is False


def test_stale_low_importance_decays_to_fading() -> None:
    old = NOW - timedelta(days=120)  # far past last access, decay_S default 1.0 → retrievability ~0
    s = compute_strength(_note("x", importance=0.1, last_accessed=old, created=old), NOW)
    assert s.band == "fading"
    assert s.at_risk is True  # last_accessed > 60d


def test_low_confidence_flags_at_risk_regardless_of_band() -> None:
    s = compute_strength(_note("x", importance=1.0, confidence=0.2), NOW)
    assert s.band == "evergreen"  # strong by S
    assert s.at_risk is True  # but confidence < 0.4


def test_memory_strength_and_list_active(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    memory.add("I drive a Ferrari")
    active = memory.list_active()
    assert len(active) == 2
    s = memory.strength(active[0].id)
    assert s.band in {"evergreen", "budding", "fading"}
    assert 0.0 <= s.value <= 1.0
