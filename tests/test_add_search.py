"""Memory add/search wiring tests (P1 units 5-6): the offline add → recall loop.

Uses the conftest ``memory`` fixture (HashEmbedder + llm=None + FrozenClock) — the
offline default (I5/G6). Unit 5 covers add→get + init/embedder guard + the single
WriteCore persist path (I15); unit 6 adds the search-recall cases.
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Scope
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
