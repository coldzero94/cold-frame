"""Store conflict/edit primitives (P2 unit 1): supersede + set_status + edges.

supersede is the single-txn bi-temporal commit (archive old, link new) that the
deterministic conflict path (P2-4) builds on. Archive-not-delete (I2): the old row
stays, revivable; only valid_at/expired_at + status change.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cold_frame.exceptions import NoteNotFound, StoreError
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Edge, Note, Scope, Source
from cold_frame.store.sqlite import SQLiteStore

T1 = datetime(2026, 1, 1, tzinfo=UTC)  # old fact valid-from
T2 = datetime(2026, 6, 1, tzinfo=UTC)  # new fact valid-from
NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)  # transaction time (clock)


class _Clock:
    def __init__(self, t: datetime) -> None:
        self._t = t

    def now(self) -> datetime:
        return self._t


def _note(nid: str, content: str, valid_at: datetime) -> Note:
    return Note(
        id=nid,
        content=content,
        memory_type="semantic",
        scope=Scope(),
        created_at=valid_at,
        valid_at=valid_at,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=valid_at)],
    )


def _emb(content: str) -> object:
    return HashEmbedder().embed_one(content)


@pytest.fixture
def store(db_path: str) -> SQLiteStore:
    s = SQLiteStore(db_path, embedder=HashEmbedder(), clock=_Clock(NOW))
    s.migrate()
    return s


def test_supersede_archives_old_inserts_new_and_links(store: SQLiteStore) -> None:
    old = _note("old", "works at Vessl", T1)
    store.add_note(old, _emb(old.content))
    new = _note("new", "works at Anthropic", T2)
    store.supersede("old", new, _emb(new.content))

    got = {n.id: n for n in store.get_notes(["old", "new"])}
    assert got["old"].status == "archived"
    assert got["old"].invalid_at == T2  # valid-time end = new.valid_at
    assert got["old"].expired_at == NOW  # transaction-time end = clock.now() (C3)
    assert got["old"].version == 2  # archiving is a new version of old
    assert got["new"].status == "active"

    # archive-not-delete (I2): both rows present
    assert int(store._conn.execute("SELECT count(*) FROM notes").fetchone()[0]) == 2
    # supersedes edge new -> old
    edges = store.neighbors(["new"], relations=["supersedes"])
    assert any(
        e.src_id == "new" and e.dst_id == "old" and e.relation == "supersedes" for e in edges
    )
    # a conflict version snapshot for old + the new note's history
    assert int(store._conn.execute("SELECT count(*) FROM note_history").fetchone()[0]) >= 2


def test_supersede_unknown_old_raises(store: SQLiteStore) -> None:
    with pytest.raises((NoteNotFound, StoreError)):
        store.supersede("nope", _note("n", "x text here", T2), _emb("x text here"))


def test_set_status_archive_and_revive(store: SQLiteStore) -> None:
    n = _note("n", "dark roast coffee", T1)
    store.add_note(n, _emb(n.content))
    store.set_status("n", "archived")
    assert store.get_notes(["n"])[0].status == "archived"
    store.set_status("n", "active")  # revive: has a source → provenance trigger passes
    assert store.get_notes(["n"])[0].status == "active"


def test_supersede_rolls_back_on_mid_txn_failure(
    store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _note("old", "works at Vessl", T1)
    store.add_note(old, _emb(old.content))

    def _boom(note_id: str, emb: object) -> None:
        raise RuntimeError("simulated mid-supersede failure")

    monkeypatch.setattr(store, "_insert_vec", _boom)
    with pytest.raises(StoreError):
        store.supersede("old", _note("new", "works at Anthropic", T2), _emb("works at Anthropic"))

    # full ROLLBACK (I3): old stays active/unversioned, new absent, no supersedes edge
    old_after = store.get_notes(["old"])[0]
    assert old_after.status == "active" and old_after.version == 1
    assert store.get_notes(["new"]) == []
    assert store.neighbors(["new"], relations=["supersedes"]) == []


def test_add_note_provenance_guard_rejects_sourceless_active(store: SQLiteStore) -> None:
    """I14: an active, high-confidence, source-less note is refused at the INSERT guard."""
    bad = Note(
        id="bad",
        content="a high-confidence fact with no provenance",
        memory_type="semantic",
        scope=Scope(),
        created_at=T1,
        confidence=1.0,
        sources=[],
    )
    with pytest.raises(StoreError):
        store.add_note(bad, _emb(bad.content))
    assert store.get_notes(["bad"]) == []


def test_supersede_provenance_guard_rejects_sourceless_active(store: SQLiteStore) -> None:
    """I14: supersede also mints an active note → same guard applies (was a missed INSERT path)."""
    store.add_note(_note("old", "works at Vessl", T1), _emb("works at Vessl"))
    bad_new = Note(
        id="new",
        content="works at Anthropic",
        memory_type="semantic",
        scope=Scope(),
        created_at=T2,
        valid_at=T2,
        confidence=1.0,
        sources=[],  # active + high-confidence + no provenance → must be refused
    )
    with pytest.raises(StoreError):
        store.supersede("old", bad_new, _emb(bad_new.content))
    assert store.get_notes(["new"]) == []  # rejected before any write
    assert store.get_notes(["old"])[0].status == "active"  # old untouched


def test_add_edge_and_neighbors_with_relation_filter(store: SQLiteStore) -> None:
    store.add_note(_note("a", "alpha topic", T1), _emb("alpha topic"))
    store.add_note(_note("b", "beta topic", T1), _emb("beta topic"))
    store.add_edge(Edge(src_id="a", dst_id="b", relation="relates_to", created_at=NOW))

    ns = store.neighbors(["a"])
    assert any(e.src_id == "a" and e.dst_id == "b" and e.relation == "relates_to" for e in ns)
    assert store.neighbors(["a"], relations=["supersedes"]) == []  # relation filter
