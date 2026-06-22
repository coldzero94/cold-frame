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
from cold_frame.models import ConflictVerdict, Note, Scope, Source
from cold_frame.store.sqlite import SQLiteStore
from cold_frame.write.core import WriteCore

from tests.conftest import FrozenClock, ScriptedLLM

T1 = datetime(2026, 1, 1, tzinfo=UTC)  # earlier belief valid-from
T2 = datetime(2026, 6, 1, tzinfo=UTC)  # later belief valid-from
T3 = datetime(2026, 9, 1, tzinfo=UTC)  # latest
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


def test_classify_iterates_past_nearest_to_find_conflict(
    db_path: str, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contradiction at rank 2 is found even when the nearest neighbor is a band non-dup."""
    emb = HashEmbedder()
    store = SQLiteStore(db_path, embedder=emb, clock=frozen_clock)
    store.migrate()

    def _seed(nid: str, content: str, valid: datetime) -> None:
        note = Note(
            id=nid,
            content=content,
            memory_type="semantic",
            scope=Scope(),
            created_at=valid,
            valid_at=valid,
            sources=[Source(kind="message", ref="m", content_hash="h", observed_at=valid)],
        )
        store.add_note(note, emb.embed_one(content))

    _seed("A", "I work at Anthropic on AI research", T2)  # rank-0 band near-dup (judged unrelated)
    _seed("B", "I work at Vessl", T1)  # rank-1 conflict-range contradiction
    llm = ScriptedLLM(
        {
            TaskTag.DEDUP_BATCH: LLMResult(
                parsed=ConflictVerdict(relation="unrelated", confidence=0.9)
            ),
            TaskTag.CONFLICT_JUDGE: LLMResult(
                parsed=ConflictVerdict(relation="contradiction", confidence=0.9)
            ),
        }
    )
    wc = WriteCore(store, embedder=emb, llm=llm, clock=frozen_clock)
    # force the neighbor ranking: A in the dedup band, B in the conflict range
    monkeypatch.setattr(store, "knn", lambda *a, **k: [("A", 0.85), ("B", 0.70)])

    cand = Note(
        id="C",
        content="I work at Anthropic",
        memory_type="semantic",
        scope=Scope(),
        created_at=T3,
        valid_at=T3,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=T3)],
    )
    kind, payload = wc._classify(cand, emb.embed_one(cand.content), Scope())
    assert (kind, payload) == ("supersede", "B")  # scanned past A's band non-dup to B's conflict


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
