"""SQLiteStore retrieval tests (P1 unit 3): bm25 (FTS5) + knn (numpy cosine) + reinforce.

The two retrieval channels feed the hybrid RRF fuse (unit 6); reinforce is the
recall side-effect (forgetting curve). All offline, deterministic (FrozenClock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cold_frame.constants import ACCESS_LOG_CAP_PER_NOTE, REINFORCE_DECAY_INC
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Note, Scope, Source
from cold_frame.store.sqlite import SQLiteStore

_INSTANT = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
_LATER = _INSTANT + timedelta(hours=3)


def _note(nid: str, content: str, *, scope: Scope | None = None) -> Note:
    return Note(
        id=nid,
        content=content,
        memory_type="episodic",
        scope=scope or Scope(),
        created_at=_INSTANT,
        valid_at=_INSTANT,
        sources=[
            Source(kind="message", ref="m1", role="user", content_hash="h", observed_at=_INSTANT)
        ],
    )


def _migrated(db_path: str) -> SQLiteStore:
    s = SQLiteStore(db_path, embedder=HashEmbedder())
    s.migrate()
    return s


def _add(store: SQLiteStore, nid: str, content: str, scope: Scope | None = None) -> None:
    emb = HashEmbedder().embed_one(content)
    store.add_note(_note(nid, content, scope=scope), emb)


# ── bm25 + knn recall ─────────────────────────────────────────────────────────
def test_bm25_and_knn_recall(db_path: str) -> None:
    store = _migrated(db_path)
    a = Scope(user_id="a")
    _add(store, "coffee", "dark roast coffee", a)
    _add(store, "pasta", "pasta recipe with garlic", a)

    bm = store.bm25("coffee", 10, scope=a, statuses=["active"])
    assert bm  # non-empty
    assert bm[0][0] == "coffee"  # best match first

    kn = store.knn(HashEmbedder().embed_one("coffee"), 10, scope=a, statuses=["active"])
    assert kn
    ids = [nid for nid, _ in kn]
    assert "coffee" in ids
    assert all(0.0 <= cos <= 1.0 for _, cos in kn)  # L2-normalized → cosine in [0,1]


def test_scope_isolation(db_path: str) -> None:
    store = _migrated(db_path)
    _add(store, "x", "dark roast coffee", Scope(user_id="a"))
    other = Scope(user_id="b")
    assert store.bm25("coffee", 10, scope=other, statuses=["active"]) == []
    assert store.knn(HashEmbedder().embed_one("coffee"), 10, scope=other, statuses=["active"]) == []


def test_knn_embedder_id_hardfilter(db_path: str) -> None:
    store = _migrated(db_path)
    _add(store, "keep", "dark roast coffee")
    _add(store, "stale", "espresso beans")
    # simulate a vector written by a different (mixed-dim) embedder
    store._conn.execute("UPDATE note_vec SET embedder_id='other' WHERE note_id='stale'")

    ids = [
        nid
        for nid, _ in store.knn(
            HashEmbedder().embed_one("coffee"), 10, scope=Scope(), statuses=["active"]
        )
    ]
    assert "keep" in ids
    assert "stale" not in ids  # excluded: embedder_id != current (I10)


def test_status_filter_excludes_non_active(db_path: str) -> None:
    store = _migrated(db_path)
    _add(store, "act", "dark roast coffee")
    store._conn.execute("UPDATE notes SET status='archived' WHERE id='act'")
    assert store.bm25("coffee", 10, scope=Scope(), statuses=["active"]) == []
    assert (
        store.knn(HashEmbedder().embed_one("coffee"), 10, scope=Scope(), statuses=["active"]) == []
    )


# ── reinforce (forgetting curve side-effect) ───────────────────────────────────
def test_reinforce_updates_and_caps_access_log(db_path: str) -> None:
    store = _migrated(db_path)
    _add(store, "n", "dark roast coffee")

    store.reinforce(["n"], now=_LATER)
    note = store.get_notes(["n"])[0]
    assert note.access_count == 1
    assert note.last_accessed == _LATER
    assert note.decay_S == 1.0 + REINFORCE_DECAY_INC  # 1.5
    assert _alog(store, "n") == 1

    for _ in range(ACCESS_LOG_CAP_PER_NOTE):  # 50 more → 51 total inserts
        store.reinforce(["n"], now=_LATER)
    assert store.get_notes(["n"])[0].access_count == 1 + ACCESS_LOG_CAP_PER_NOTE
    assert _alog(store, "n") == ACCESS_LOG_CAP_PER_NOTE  # capped at 50 (no unbounded growth)


def _alog(store: SQLiteStore, nid: str) -> int:
    return int(
        store._conn.execute("SELECT count(*) FROM access_log WHERE note_id=?", (nid,)).fetchone()[0]
    )
