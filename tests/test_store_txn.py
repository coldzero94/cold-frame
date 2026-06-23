"""SQLiteStore plumbing tests (P1 units 1-2): lifecycle, schema, single-txn write.

These are *plumbing* unit tests (CLAUDE.md §2): engine behavior is proven by golden
YAML cases, but the Store transaction/migration machinery is proven here. They run
offline with HashEmbedder + a FrozenClock, no network, no keys.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from cold_frame.exceptions import NoteNotFound, StoreError
from cold_frame.llm.base import EmbedderMeta, HashEmbedder
from cold_frame.models import Note, Scope, Source
from cold_frame.store.sqlite import SQLiteStore

_INSTANT = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _note(nid: str, content: str, *, scope: Scope | None = None) -> Note:
    """A minimal episodic Note with one message source (frozen instant)."""
    return Note(
        id=nid,
        content=content,
        memory_type="episodic",
        scope=scope or Scope(),
        created_at=_INSTANT,
        valid_at=_INSTANT,
        sources=[
            Source(kind="message", ref="m1", role="user", content_hash="h1", observed_at=_INSTANT)
        ],
    )


def _count(store: SQLiteStore, table: str) -> int:
    return int(store._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


@pytest.fixture
def store(db_path: str) -> SQLiteStore:
    s = SQLiteStore(db_path, embedder=HashEmbedder())
    s.migrate()
    return s


# The 10 core tables migration 0->1 must create (data-layer §1). FTS5 also creates
# shadow tables (note_fts_data/_idx/...); we assert a subset so those are allowed.
EXPECTED_TABLES = {
    "notes",
    "note_fts",
    "note_vec",
    "edges",
    "note_history",
    "sources",
    "access_log",
    "events",
    "jobs",
    "meta",
}


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


# ── unit 1: lifecycle / migrate / meta ───────────────────────────────────────
def test_migrate_fresh_db_embedder_meta_none(db_path: str) -> None:
    """A brand-new store, before migrate(), has no embedder meta yet (fresh db)."""
    store = SQLiteStore(db_path, embedder=HashEmbedder())
    assert store.embedder_meta() is None


def test_migrate_creates_schema_and_meta(db_path: str) -> None:
    store = SQLiteStore(db_path, embedder=HashEmbedder())
    store.migrate()

    # integrity_check passes (doctor invariant)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()

    # every core table exists
    assert _tables(db_path) >= EXPECTED_TABLES

    # meta seeded: schema_version + embedder identity (dim read from Embedder.meta, I8)
    assert store.get_meta("schema_version") == "1"
    assert store.embedder_meta() == EmbedderMeta(embedder_id="hash", dim=256)

    # idempotent: a second migrate() is a no-op (no error, same tables, same version)
    before = _tables(db_path)
    store.migrate()
    assert _tables(db_path) == before
    assert store.get_meta("schema_version") == "1"


def test_migrate_is_reopen_safe(db_path: str) -> None:
    """A second SQLiteStore opening the same migrated db sees the persisted meta."""
    SQLiteStore(db_path, embedder=HashEmbedder()).migrate()
    reopened = SQLiteStore(db_path, embedder=HashEmbedder())
    reopened.migrate()  # no-op
    assert reopened.get_meta("schema_version") == "1"
    assert reopened.embedder_meta() == EmbedderMeta(embedder_id="hash", dim=256)


# ── unit 2: add_note single-txn dual-write + get_notes + append_event ─────────
def test_add_note_single_txn_roundtrip(store: SQLiteStore) -> None:
    note = _note("n1", "dark roast coffee")
    emb = HashEmbedder().embed_one(note.content)
    store.add_note(note, emb)

    # full hydration round-trip (scope + sources reconstructed)
    got = store.get_notes(["n1"])
    assert len(got) == 1
    assert got[0] == note

    # I10 doctor invariant: every grain co-written in one txn
    assert _count(store, "notes") == _count(store, "note_fts") == _count(store, "note_vec") == 1
    assert _count(store, "sources") == 1
    assert _count(store, "note_history") == 1
    # one co-written create event (I3)
    assert (
        int(store._conn.execute("SELECT count(*) FROM events WHERE op='create'").fetchone()[0]) == 1
    )


def test_doctor_reports_fts_integrity_and_stale_vectors(store: SQLiteStore) -> None:
    note = _note("d1", "a healthy note")
    store.add_note(note, HashEmbedder().embed_one(note.content))
    h = store.doctor()
    assert h["fts_integrity"] == "ok"  # real FTS5 integrity-check, not the vacuous row count
    assert h["stale_vectors"] == 0  # all vectors written by the current embedder

    # a vector from a different embedder is counted as stale (KNN would exclude it, I10)
    store._conn.execute(
        "UPDATE note_vec SET embedder_id = 'old-embedder-v0' WHERE note_id = ?", ("d1",)
    )
    assert store.doctor()["stale_vectors"] == 1


def test_get_notes_preserves_order_and_skips_unknown(store: SQLiteStore) -> None:
    emb = HashEmbedder()
    store.add_note(_note("a", "first fact"), emb.embed_one("first fact"))
    store.add_note(_note("b", "second fact"), emb.embed_one("second fact"))
    got = store.get_notes(["b", "missing", "a"])
    assert [n.id for n in got] == ["b", "a"]  # requested order kept, unknown skipped


def test_add_note_emb_none_inserts_no_vec_row(store: SQLiteStore) -> None:
    store.add_note(_note("n2", "green tea please"), None)
    assert _count(store, "notes") == 1
    assert _count(store, "note_fts") == 1
    assert _count(store, "note_vec") == 0  # no-embed path (I5): notes+fts only
    assert len(store.get_notes(["n2"])) == 1


def test_update_note_in_place_resyncs_fts_and_versions(store: SQLiteStore) -> None:
    note = _note("u1", "I prefer light roast coffee")
    store.add_note(note, HashEmbedder().embed_one(note.content))

    updated = note.model_copy(update={"content": "I prefer dark roast coffee", "version": 2})
    store.update_note(updated, update_type="manual", emb=HashEmbedder().embed_one(updated.content))

    got = store.get_notes(["u1"])[0]
    assert got.content == "I prefer dark roast coffee"
    assert got.version == 2
    # FTS re-synced: new content searchable, old term gone
    assert store.bm25("dark", 10, scope=Scope(), statuses=["active"])
    assert store.bm25("light", 10, scope=Scope(), statuses=["active"]) == []
    # no drift + one update event co-written (I3)
    assert _count(store, "notes") == _count(store, "note_fts") == _count(store, "note_vec") == 1
    assert (
        int(store._conn.execute("SELECT count(*) FROM events WHERE op='update'").fetchone()[0]) == 1
    )


def test_update_note_unknown_raises(store: SQLiteStore) -> None:
    with pytest.raises(NoteNotFound):
        store.update_note(_note("missing", "some text here"), update_type="manual")


def test_delete_removes_all_searchable_grains(store: SQLiteStore) -> None:
    note = _note("d1", "a removable fact about coffee")
    store.add_note(note, HashEmbedder().embed_one(note.content))
    store.delete("d1")
    assert store.get_notes(["d1"]) == []
    assert _count(store, "notes") == _count(store, "note_fts") == _count(store, "note_vec") == 0
    assert store.bm25("coffee", 10, scope=Scope(), statuses=["active"]) == []  # FTS grain gone
    assert (
        int(
            store._conn.execute(
                "SELECT count(*) FROM events WHERE op='delete' AND entity_id=?", ("d1",)
            ).fetchone()[0]
        )
        == 1  # the removal is audited
    )


def test_delete_unknown_raises(store: SQLiteStore) -> None:
    with pytest.raises(NoteNotFound):
        store.delete("ghost")


def test_delete_cascades_sources_and_edges(store: SQLiteStore) -> None:
    a = _note("a1", "I work at Vessl")
    store.add_note(a, HashEmbedder().embed_one(a.content))
    new = _note("a2", "I work at Anthropic")  # supersede → 'supersedes' edge a2→a1 + archives a1
    store.supersede("a1", new, HashEmbedder().embed_one(new.content))
    assert _row_count(store, "sources", "note_id", "a1") >= 1
    assert _row_count(store, "edges", "dst_id", "a1") >= 1

    store.delete("a1")
    assert store.get_notes(["a1"]) == []
    assert _row_count(store, "sources", "note_id", "a1") == 0  # FK cascade
    assert _row_count(store, "edges", "dst_id", "a1") == 0  # FK cascade
    assert _row_count(store, "edges", "src_id", "a1") == 0


def _row_count(store: SQLiteStore, table: str, col: str, val: str) -> int:
    return int(
        store._conn.execute(f"SELECT count(*) FROM {table} WHERE {col}=?", (val,)).fetchone()[0]
    )


def test_get_history_and_as_of(store: SQLiteStore) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 6, 1, tzinfo=UTC)
    emb = HashEmbedder()
    old = Note(
        id="h1",
        content="I work at Vessl",
        memory_type="episodic",
        scope=Scope(),
        created_at=t1,
        valid_at=t1,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=t1)],
    )
    store.add_note(old, emb.embed_one(old.content))
    new = old.model_copy(update={"id": "h2", "content": "I work at Anthropic", "valid_at": t2})
    store.supersede("h1", new, emb.embed_one(new.content))

    hist = store.get_history("h1")  # v1 (added) + v2 (archived at supersede)
    assert [h.version for h in hist] == [1, 2]
    assert hist[0].content == "I work at Vessl"

    mid = datetime(2026, 3, 1, tzinfo=UTC)
    assert [n.content for n in store.as_of(["h1"], at=mid)] == ["I work at Vessl"]  # belief then
    assert store.as_of(["h1"], at=datetime(2025, 1, 1, tzinfo=UTC)) == []  # not yet present
    # after h1's valid-time end (invalid_at=t2) it is no longer in effect → ABC contract
    # valid_at<=at<invalid_at. (The v1 history snapshot still carries invalid_at=None, so the
    # gate must apply to the chosen highest-effective version, not each snapshot.)
    assert store.as_of(["h1"], at=datetime(2026, 9, 1, tzinfo=UTC)) == []  # invalidated by then


def test_backup_before_upgrade_snapshots_the_db(db_path: str) -> None:
    store = SQLiteStore(db_path, embedder=HashEmbedder())
    store.migrate()
    store.add_note(_note("n1", "keep me through the upgrade"), HashEmbedder().embed_one("keep me"))
    store._backup_before_upgrade(1)  # simulate an upgrade from v1 → snapshot first

    bak = Path(f"{db_path}.bak.1")
    assert bak.exists()
    snap = sqlite3.connect(str(bak))
    try:
        assert snap.execute("SELECT count(*) FROM notes").fetchone()[0] == 1  # consistent copy
    finally:
        snap.close()


def test_fresh_migrate_creates_no_backup(db_path: str) -> None:
    SQLiteStore(db_path, embedder=HashEmbedder()).migrate()  # current 0 → 1: a fresh install
    assert not list(Path(db_path).parent.glob("*.bak.*"))  # nothing to protect yet


def test_iter_events_yields_in_hlc_order(store: SQLiteStore) -> None:
    for i in range(3):
        store.add_note(_note(f"e{i}", f"fact {i} here"), HashEmbedder().embed_one(f"fact {i}"))
    events = list(store.iter_events())
    assert [e.op for e in events] == ["create", "create", "create"]
    assert [e.hlc for e in events] == sorted(e.hlc for e in events)  # hlc-ordered
    assert {e.entity_id for e in events} == {"e0", "e1", "e2"}


def test_snapshot_is_a_complete_restorable_copy(store: SQLiteStore, db_path: str) -> None:
    store.add_note(_note("s1", "snapshot me"), HashEmbedder().embed_one("snapshot me"))
    snap = f"{db_path}.snap"
    store.snapshot(snap)
    assert Path(snap).exists()
    copy = SQLiteStore(snap, embedder=HashEmbedder())  # open the snapshot as a db
    assert len(copy.get_notes(["s1"])) == 1  # consistent, complete
    assert copy.doctor()["integrity"] == "ok"
    copy.close()


def test_update_note_optimistic_version_lock(store: SQLiteStore) -> None:
    note = _note("v1", "original text here")  # version 1
    store.add_note(note, HashEmbedder().embed_one(note.content))
    first = note.model_copy(update={"content": "first edit", "version": 2})
    store.update_note(first, update_type="manual", emb=HashEmbedder().embed_one("first edit"))

    # a stale caller that also read version 1 and tries 1→2 after the DB already moved to 2
    stale = note.model_copy(update={"content": "stale edit", "version": 2})
    with pytest.raises(StoreError, match="version conflict"):
        store.update_note(stale, update_type="manual", emb=HashEmbedder().embed_one("stale"))
    assert store.get_notes(["v1"])[0].content == "first edit"  # stale write rejected


def test_update_note_rollback_on_failure(
    store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _note("u9", "original roast content")
    store.add_note(note, HashEmbedder().embed_one(note.content))

    def _boom(note_id: str, emb: np.ndarray) -> None:
        raise RuntimeError("simulated mid-update failure")

    monkeypatch.setattr(store, "_insert_vec", _boom)
    updated = note.model_copy(update={"content": "changed content", "version": 2})
    with pytest.raises(StoreError):
        store.update_note(updated, update_type="manual", emb=HashEmbedder().embed_one("changed"))

    # full ROLLBACK (I3): content/version unchanged, FTS not drifted by the delete+reinsert
    got = store.get_notes(["u9"])[0]
    assert got.content == "original roast content" and got.version == 1
    assert _count(store, "notes") == _count(store, "note_fts") == _count(store, "note_vec") == 1
    assert store.bm25("original", 10, scope=Scope(), statuses=["active"])


def test_add_note_rollback_on_failure(store: SQLiteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    note = _note("n3", "should be rolled back")
    emb = HashEmbedder().embed_one(note.content)

    def _boom(note_id: str, emb: np.ndarray) -> None:
        raise RuntimeError("simulated mid-txn failure")

    monkeypatch.setattr(store, "_insert_vec", _boom)
    with pytest.raises(StoreError):
        store.add_note(note, emb)

    # full ROLLBACK: no half-write anywhere (I3)
    assert _count(store, "notes") == 0
    assert _count(store, "note_fts") == 0
    assert _count(store, "note_vec") == 0
    assert _count(store, "events") == 0
