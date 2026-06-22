"""SQLiteStore plumbing tests (P1 units 1-2): lifecycle, schema, single-txn write.

These are *plumbing* unit tests (CLAUDE.md §2): engine behavior is proven by golden
YAML cases, but the Store transaction/migration machinery is proven here. They run
offline with HashEmbedder + a FrozenClock, no network, no keys.
"""

from __future__ import annotations

import sqlite3

from cold_frame.llm.base import EmbedderMeta, HashEmbedder
from cold_frame.store.sqlite import SQLiteStore

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
    assert EXPECTED_TABLES <= _tables(db_path)

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
