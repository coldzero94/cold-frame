"""SQLiteStore — the v1 ``Store`` adapter (single ``.db`` file).

Dialect-specific bits (FTS5 / numpy-KNN over BLOB / JSON-as-TEXT) stay behind this
adapter (I8); core never imports sqlite-specific idioms. ALL writes go through ONE
``BEGIN IMMEDIATE``…``COMMIT`` transaction (I3); the canonical vector is a float32
little-endian BLOB and the ``[vec]`` index (later) sits on top of it (I10).

The DDL below follows ``docs/build/data-layer.md §1`` but is reconciled to the pinned
``models.py`` contract where the doc diverged (CLAUDE.md conflict rule — code wins):
``status`` is the 3-value set {active, archived, deleted} (no ``pending``) and
quarantine is the ``held_for_human`` / ``quarantined`` / ``triage_reason`` flag columns.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np

from cold_frame.constants import (
    BUSY_TIMEOUT_MS,
    EMBED_METRIC,
    SCHEMA_VERSION,
    WAL_AUTOCHECKPOINT,
)
from cold_frame.exceptions import StoreError
from cold_frame.llm.base import Clock, Embedder, EmbedderMeta, SystemClock
from cold_frame.models import (
    Edge,
    EdgeRelation,
    Note,
    Scope,
    StatusLiteral,
    UpdateType,
)
from cold_frame.store.base import Event, Job, PurgeReport, Store

# Default provenance stamp for the DB ``sources.extractor`` column (data-layer §1).
# The pydantic ``Source`` model carries no ``extractor`` field (code wins), so the
# adapter supplies this storage-internal value at write time.
_DEFAULT_EXTRACTOR: str = "pipeline:v1"


def _to_iso(dt: datetime) -> str:
    """tz-aware datetime -> ISO8601-UTC TEXT with a ``Z`` suffix (I8)."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _from_iso(s: str) -> datetime:
    """ISO8601-UTC TEXT -> tz-aware UTC datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── schema: migration 0 -> 1 (full v1 DDL, idempotent via IF NOT EXISTS) ──────
_DDL_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id             TEXT PRIMARY KEY,
  content        TEXT NOT NULL,
  memory_type    TEXT NOT NULL,
  keywords       TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  context        TEXT NOT NULL DEFAULT '',
  confidence     REAL NOT NULL DEFAULT 1.0,
  importance     REAL NOT NULL DEFAULT 0.5,
  user_id        TEXT NOT NULL DEFAULT 'default',
  agent_id       TEXT,
  session_id     TEXT,
  status         TEXT NOT NULL DEFAULT 'active',   -- active|archived|deleted (3-value, code wins)
  version        INTEGER NOT NULL DEFAULT 1,
  held_for_human INTEGER NOT NULL DEFAULT 0,
  quarantined    INTEGER NOT NULL DEFAULT 0,       -- G2 flag column (excluded from default search)
  triage_reason  TEXT,
  pinned         INTEGER NOT NULL DEFAULT 0,
  redaction      TEXT,                             -- null | 'pii' | 'secret_tombstone'
  created_at     TEXT NOT NULL,
  expired_at     TEXT,
  valid_at       TEXT,
  invalid_at     TEXT,
  last_accessed  TEXT,
  access_count   INTEGER NOT NULL DEFAULT 0,
  decay_S        REAL NOT NULL DEFAULT 1.0,
  content_hash   TEXT NOT NULL,
  embedder_id    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_scope    ON notes(user_id, agent_id, session_id, status);
CREATE INDEX IF NOT EXISTS idx_notes_type     ON notes(memory_type, status);
CREATE INDEX IF NOT EXISTS idx_notes_valid    ON notes(valid_at, invalid_at);
CREATE INDEX IF NOT EXISTS idx_notes_triage   ON notes(held_for_human) WHERE held_for_human = 1;
CREATE INDEX IF NOT EXISTS idx_notes_hash     ON notes(content_hash);
CREATE INDEX IF NOT EXISTS idx_notes_embedder ON notes(embedder_id) WHERE status = 'active';

CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
  content, keywords, tags,
  content='notes', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS note_vec (
  note_id     TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
  embedder_id TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  embedding   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vec_embedder ON note_vec(embedder_id);

CREATE TABLE IF NOT EXISTS edges (
  src_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  dst_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  relation   TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL,
  valid_at   TEXT,
  invalid_at TEXT,
  PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);

CREATE TABLE IF NOT EXISTS note_history (
  id          TEXT NOT NULL,
  version     INTEGER NOT NULL,
  snapshot    TEXT NOT NULL,
  update_type TEXT NOT NULL,
  changed_at  TEXT NOT NULL,
  PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS sources (
  note_id      TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  ref          TEXT NOT NULL,
  role         TEXT,
  content_hash TEXT NOT NULL,
  extractor    TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  observed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_note ON sources(note_id);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(content_hash);

CREATE TABLE IF NOT EXISTS access_log (
  note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  kind    TEXT NOT NULL DEFAULT 'search'
);
CREATE INDEX IF NOT EXISTS idx_access_note_ts ON access_log(note_id, ts);

CREATE TABLE IF NOT EXISTS events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL UNIQUE,
  device_id    TEXT NOT NULL,
  hlc          TEXT NOT NULL,
  entity       TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  op           TEXT NOT NULL,
  content_hash TEXT,
  payload      TEXT NOT NULL,
  ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_hlc    ON events(hlc);

CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  attempts     INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  dedup_key    TEXT,
  run_after    TEXT NOT NULL,
  locked_by    TEXT,
  locked_at    TEXT,
  last_error   TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, run_after);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup
  ON jobs(dedup_key) WHERE status = 'pending' AND dedup_key IS NOT NULL;

-- provenance invariant (I14): an active high-confidence note needs >=1 source.
-- Fires on the self-edit/UPDATE path; the normal add_note INSERT writes sources first.
CREATE TRIGGER IF NOT EXISTS trg_provenance_active
BEFORE UPDATE OF status ON notes
WHEN NEW.status = 'active' AND NEW.confidence >= 0.4
     AND NEW.redaction IS NOT 'secret_tombstone'
     AND (SELECT COUNT(*) FROM sources WHERE note_id = NEW.id) = 0
BEGIN
  SELECT RAISE(ABORT, 'provenance invariant: active high-confidence note needs >=1 source');
END;
"""

# (target_version, ddl), append-only + in order; each step is idempotent.
_MIGRATIONS: list[tuple[int, str]] = [(1, _DDL_V1)]
# Tie the migration list to the frozen schema version (constants.py is the SoT):
# bumping SCHEMA_VERSION without appending a migration fails fast here.
assert _MIGRATIONS[-1][0] == SCHEMA_VERSION, "migrations must reach SCHEMA_VERSION"


class SQLiteStore(Store):
    """Single-file SQLite adapter (one ``.db``: notes + FTS + vectors + edges + jobs)."""

    def __init__(
        self,
        db_path: str,
        *,
        embedder: Embedder | None = None,
        clock: Clock | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._clock: Clock = clock or SystemClock()
        self._new_id: Callable[[], str] = new_id or (lambda: uuid.uuid4().hex)
        self._conn = self._open(db_path)

    # ── connection / PRAGMAs (data-layer §3.1) ──────────────────────────────
    @staticmethod
    def _open(db_path: str) -> sqlite3.Connection:
        # isolation_level=None → autocommit; transactions are explicit BEGIN IMMEDIATE (I3).
        conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT}")
        conn.execute("PRAGMA secure_delete=ON")
        return conn

    # ── lifecycle ──────────────────────────────────────────────────────────
    def migrate(self) -> None:
        try:
            current = self._schema_version()
            for version, ddl in _MIGRATIONS:
                if version <= current:
                    continue
                self._conn.executescript(ddl)  # idempotent (IF NOT EXISTS)
                self.set_meta("schema_version", str(version))
                self._conn.execute(f"PRAGMA user_version = {version}")
            self._seed_meta_once()
        except sqlite3.Error as exc:  # pragma: no cover - exercised via rollback tests later
            raise StoreError(f"migrate failed: {exc}") from exc

    def _schema_version(self) -> int:
        v = self.get_meta("schema_version")
        return int(v) if v is not None else 0

    def _seed_meta_once(self) -> None:
        """Write the one-time identity meta keys (device_id/hlc/embedder/...) if absent."""
        if self.get_meta("device_id") is None:
            device_id = self._new_id()
            self.set_meta("device_id", device_id)
            self.set_meta("hlc_last", f"0:0:{device_id}")
            self.set_meta("vec_backend", "numpy")
            self.set_meta("created_at", _to_iso(self._clock.now()))
        if self.get_meta("embedder_id") is None and self._embedder is not None:
            self.set_embedder_meta(self._embedder.meta)

    def embedder_meta(self) -> EmbedderMeta | None:
        embedder_id = self.get_meta("embedder_id")
        dim = self.get_meta("embedder_dim")
        if embedder_id is None or dim is None:
            return None
        return EmbedderMeta(embedder_id=embedder_id, dim=int(dim))

    def set_embedder_meta(self, meta: EmbedderMeta) -> None:
        self.set_meta("embedder_id", meta.embedder_id)
        self.set_meta("embedder_dim", str(meta.dim))
        self.set_meta("embedder_metric", EMBED_METRIC)

    def get_meta(self, key: str) -> str | None:
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return None  # meta table not created yet (fresh db, pre-migrate)
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        # Upsert WITHOUT INSERT OR REPLACE (I8): ON CONFLICT keeps it portable + trigger-safe.
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @contextmanager
    def in_transaction(self) -> Iterator[None]:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ── atomic write (units 2+) ─────────────────────────────────────────────
    def add_note(self, note: Note, emb: np.ndarray | None) -> None:
        raise NotImplementedError

    def update_note(
        self, note: Note, *, update_type: UpdateType, emb: np.ndarray | None = None
    ) -> None:
        raise NotImplementedError

    def supersede(self, old_id: str, new: Note, emb: np.ndarray | None) -> None:
        raise NotImplementedError

    def get_notes(self, ids: list[str]) -> list[Note]:
        raise NotImplementedError

    def set_status(
        self, id: str, status: StatusLiteral, *, invalid_at: datetime | None = None
    ) -> None:
        raise NotImplementedError

    # ── retrieval (unit 3) ──────────────────────────────────────────────────
    def knn(
        self,
        emb: np.ndarray,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    def bm25(
        self,
        query: str,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    def reinforce(self, ids: list[str], *, now: datetime) -> None:
        raise NotImplementedError

    # ── edges ─────────────────────────────────────────────────────────────
    def add_edge(self, edge: Edge) -> None:
        raise NotImplementedError

    def neighbors(
        self, ids: list[str], *, relations: list[EdgeRelation] | None = None
    ) -> list[Edge]:
        raise NotImplementedError

    # ── triage / quarantine reads ───────────────────────────────────────────
    def held_for_human(self, *, scope: Scope, limit: int) -> list[Note]:
        raise NotImplementedError

    def set_held_for_human(
        self, id: str, *, held: bool, quarantined: bool, reason: str | None
    ) -> None:
        raise NotImplementedError

    def by_status(
        self,
        *,
        scope: Scope,
        status: StatusLiteral,
        sort: Literal["decay", "recent", "importance"],
        limit: int,
        offset: int = 0,
    ) -> list[Note]:
        raise NotImplementedError

    def as_of(self, ids: list[str], *, at: datetime) -> list[Note]:
        raise NotImplementedError

    # ── jobs (durable queue) ────────────────────────────────────────────────
    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
        run_after: datetime | None = None,
    ) -> str:
        raise NotImplementedError

    def lease_job(self, *, worker: str, now: datetime) -> Job | None:
        raise NotImplementedError

    def finish_job(self, id: str) -> None:
        raise NotImplementedError

    def fail_job(self, id: str, *, error: str, retry_after: datetime | None) -> None:
        raise NotImplementedError

    # ── event log / export ──────────────────────────────────────────────────
    def append_event(self, ev: Event) -> None:
        raise NotImplementedError

    def iter_events(self, *, since_hlc: str | None = None) -> Iterator[Event]:
        raise NotImplementedError

    # ── secret hard-purge ───────────────────────────────────────────────────
    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        raise NotImplementedError

    # ── housekeeping ─────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()
