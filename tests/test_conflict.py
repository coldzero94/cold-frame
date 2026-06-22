"""Bi-temporal conflict + deterministic freshness (P2 unit 4).

The CONFLICT LLM only proposes "contradiction"; valid_at comparison (CODE, never the
LLM — I1) decides supersession. raw=True skips the EXTRACT LLM so the scripted LLM only
needs the CONFLICT_JUDGE entry. Contradiction pairs ("work at X/Y", cosine ~0.75) sit
below the dedup band, so only the conflict judge fires.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.models import ConflictVerdict

from tests.conftest import FrozenClock, ScriptedLLM

T1 = datetime(2026, 1, 1, tzinfo=UTC)  # earlier belief valid-from
T2 = datetime(2026, 6, 1, tzinfo=UTC)  # later belief valid-from
MID = datetime(2026, 3, 1, tzinfo=UTC)  # between


def _mem(
    db_path: str, clock: FrozenClock, *, confidence: float = 0.9, rationale: str = ""
) -> tuple[Memory, ScriptedLLM]:
    llm = ScriptedLLM(
        {
            TaskTag.CONFLICT_JUDGE: LLMResult(
                parsed=ConflictVerdict(
                    relation="contradiction", confidence=confidence, rationale=rationale
                )
            )
        }
    )
    return Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=clock), llm


def test_conflict_new_supersedes_old(db_path: str, frozen_clock: FrozenClock) -> None:
    m, llm = _mem(db_path, frozen_clock)
    old_id = m.add("I work at Vessl", raw=True, observed_at=T1).added[0].id
    res = m.add("I work at Anthropic", raw=True, observed_at=T2)

    assert res.superseded == [old_id]
    assert len(res.added) == 1 and "Anthropic" in res.added[0].content
    assert TaskTag.CONFLICT_JUDGE in llm.calls
    assert m.get(old_id).status == "archived"
    assert m.get(old_id).invalid_at == T2  # valid-time end = new.valid_at (C3)

    assert "Anthropic" in m.search("where do I work").hits[0].note.content  # current belief
    assert "Vessl" in m.search("where do I work", as_of=MID).hits[0].note.content  # belief at MID


def test_conflict_stale_new_is_bounded_not_superseding(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m, _ = _mem(db_path, frozen_clock)
    m.add("I work at Anthropic", raw=True, observed_at=T2)  # current belief (valid T2)
    res = m.add("I work at Vessl", raw=True, observed_at=T1)  # OLDER fact → stale, not a supersede

    assert res.superseded == []  # the current belief is NOT archived by an older fact
    assert len(res.added) == 1
    # the stale Vessl (valid T1, invalid_at=T2) is excluded from the default (now) search...
    assert "Anthropic" in m.search("where do I work").hits[0].note.content
    # ...but it correctly reconstructs as the belief between T1 and T2
    assert "Vessl" in m.search("where do I work", as_of=MID).hits[0].note.content


def test_conflict_tie_goes_to_triage(db_path: str, frozen_clock: FrozenClock) -> None:
    m, _ = _mem(db_path, frozen_clock)
    m.add("I work at Vessl", raw=True, observed_at=T1)
    res = m.add("I work at Anthropic", raw=True, observed_at=T1)  # SAME valid_at → cannot decide

    assert res.superseded == [] and res.added == []
    assert len(res.held) == 1
    assert res.held[0].triage_reason == "true_conflict"
    assert res.held[0].quarantined is True
    # held note is excluded from search; the original stays active
    assert "Vessl" in m.search("where do I work").hits[0].note.content


@pytest.mark.parametrize(
    ("confidence", "rationale"), [(0.01, "garbage hint"), (0.99, "confident hint")]
)
def test_freshness_disposition_ignores_llm_hint(
    db_path: str, frozen_clock: FrozenClock, confidence: float, rationale: str
) -> None:
    """I1: freshness is decided by valid_at, NOT the LLM's confidence/rationale."""
    m, _ = _mem(db_path, frozen_clock, confidence=confidence, rationale=rationale)
    old_id = m.add("I work at Vessl", raw=True, observed_at=T1).added[0].id
    res = m.add("I work at Anthropic", raw=True, observed_at=T2)
    assert res.superseded == [old_id]  # T2 > T1 supersedes regardless of the LLM hint
