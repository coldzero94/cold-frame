"""Memory add/search wiring tests (P1 units 5-6): the offline add → recall loop.

Uses the conftest ``memory`` fixture (HashEmbedder + llm=None + FrozenClock) — the
offline default (I5/G6). Unit 5 covers add→get + init/embedder guard + the single
WriteCore persist path (I15); unit 6 adds the search-recall cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Note, Scope, Source
from cold_frame.write.core import WriteCore


# ── unit 5: add → get, init guard, single persist path ────────────────────────
def test_offline_add_recall(memory: Memory) -> None:
    res = memory.add("I prefer dark roast")
    assert len(res.added) == 1
    assert res.added[0].content == "I prefer dark roast"
    assert res.superseded == [] and res.deduped == [] and res.blocked == [] and res.held == []

    got = memory.get(res.added[0].id)
    assert got.id == res.added[0].id
    assert got.content == "I prefer dark roast"


def test_memory_init_migrates_and_add_works(db_path: str) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None)
    assert len(m.add("hello world").added) == 1  # migrate ran, embedder meta written


def test_memory_embedder_mismatch_raises(db_path: str) -> None:
    Memory(db_path, embedder=HashEmbedder(), llm=None)  # seeds db meta dim=256
    with pytest.raises(EmbedderMismatchError):
        Memory(db_path, embedder=HashEmbedder(dim=128), llm=None)  # dim 128 != stored 256


def test_get_unknown_raises(memory: Memory) -> None:
    with pytest.raises(NoteNotFound):
        memory.get("does-not-exist")


def test_add_routes_through_writecore(memory: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    orig = WriteCore.commit

    def spy(self: WriteCore, candidates: list, *, scope: object, source: object = None) -> object:  # type: ignore[type-arg]
        calls.append(len(candidates))
        return orig(self, candidates, scope=scope, source=source)  # type: ignore[arg-type]

    monkeypatch.setattr(WriteCore, "commit", spy)
    memory.add("route me through the single persist path")
    assert calls == [1]  # I15: exactly one WriteCore.commit per add


# ── unit 6: offline add → search recall (P1 SPEC acceptance) ──────────────────
def test_offline_add_then_search_recall(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    memory.add("I drive a Ferrari 488 GTB")
    res = memory.search("coffee roast", k=5)
    assert res.hits  # the just-added fact is recalled
    assert "dark roast" in res.hits[0].note.content
    assert all(h.signals.rrf is not None for h in res.hits)  # rrf is the required signal
    assert res.used_tokens is None  # no token budget given
    assert res.truncated is False


def test_search_empty_query_no_match_no_raise(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    res = memory.search("zzzz nonexistent qqqq")
    assert res.hits == []  # no semantic/lexical overlap → empty, never raises


def test_search_scope_isolation(memory: Memory) -> None:
    memory.add("dark roast coffee", scope=Scope(user_id="alice"))
    assert memory.search("coffee", scope=Scope(user_id="bob")).hits == []  # cross_scope guard


def test_search_excludes_quarantined(memory: Memory) -> None:
    res = memory.add("dark roast coffee preference")
    nid = res.added[0].id
    memory._store._conn.execute("UPDATE notes SET quarantined=1 WHERE id=?", (nid,))
    assert memory.search("coffee").hits == []  # default FILTER excludes quarantined (G2)


def test_search_reinforces_returned_hits(memory: Memory) -> None:
    res = memory.add("dark roast coffee")
    nid = res.added[0].id
    memory.search("coffee")
    assert memory.get(nid).access_count == 1  # being surfaced is the reinforcement signal


def test_search_as_of_returns_belief_at_that_time(memory: Memory) -> None:
    """C3 bi-temporal hero: as_of bypasses the status filter + TRUE predicate."""
    t1 = datetime(2026, 1, 1, tzinfo=UTC)  # worked at Vessl from here
    t2 = datetime(2026, 6, 1, tzinfo=UTC)  # switched to Anthropic here
    mid = datetime(2026, 3, 1, tzinfo=UTC)  # belief checkpoint (between)

    old_id = memory.add("I work at Vessl", observed_at=t1).added[0].id
    # drive the conflict commit at the Store level (the WriteCore conflict path is P2-4)
    new = Note(
        id="new-job",
        content="I work at Anthropic",
        memory_type="episodic",
        scope=Scope(),
        created_at=t2,
        valid_at=t2,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=t2)],
    )
    memory._store.supersede(old_id, new, HashEmbedder().embed_one(new.content))

    now_hits = memory.search("where do I work").hits
    assert now_hits and "Anthropic" in now_hits[0].note.content  # current active belief

    mid_hits = memory.search("where do I work", as_of=mid).hits
    assert mid_hits and "Vessl" in mid_hits[0].note.content  # what was TRUE at `mid`


# ── audit fixes: archived recall + historical-read must not reinforce ──────────
def test_search_include_archived_surfaces_a_forgotten_note(memory: Memory) -> None:
    # include_archived is a documented public param; without as_of it was silently a no-op because
    # the "currently in effect" gate filtered archived rows back out. It must surface them.
    nid = memory.add("I deploy with ship.sh").added[0].id
    memory.forget(nid)  # archive (not delete)
    assert memory.search("deploy").hits == []  # default excludes archived
    hits = memory.search("deploy", include_archived=True).hits
    assert any(h.note.id == nid for h in hits)  # now revivable via search


def test_as_of_search_does_not_reinforce_archived_belief(memory: Memory) -> None:
    # a historical (rewind) read must not bump decay/access of the surfaced note.
    nid = memory.add("I deploy with ship.sh").added[0].id
    memory.search("deploy")  # a normal read reinforces
    bumped = memory.get(nid).access_count
    assert bumped >= 1
    memory.search("deploy", as_of=datetime(2099, 1, 1, tzinfo=UTC))  # historical read
    assert memory.get(nid).access_count == bumped  # unchanged — no reinforce on as_of
