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

import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from cold_frame.constants import (
    ACCESS_LOG_CAP_PER_NOTE,
    BUSY_TIMEOUT_MS,
    DECAY_S_CAP,
    EMBED_METRIC,
    REINFORCE_DECAY_INC,
    SCHEMA_VERSION,
    WAL_AUTOCHECKPOINT,
)
from cold_frame.exceptions import NoteNotFound, StoreError
from cold_frame.llm.base import Clock, Embedder, EmbedderMeta, SystemClock
from cold_frame.models import (
    Edge,
    EdgeRelation,
    Note,
    Scope,
    Source,
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


def _opt_iso(dt: datetime | None) -> str | None:
    return None if dt is None else _to_iso(dt)


def _opt_from_iso(s: str | None) -> datetime | None:
    return None if s is None else _from_iso(s)


def _content_hash(content: str) -> str:
    """sha256 over whitespace-collapsed, lowercased content (dedup/event grain)."""
    normalized = " ".join(content.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _vec_to_blob(emb: np.ndarray) -> bytes:
    """float32 little-endian BLOB (dim*4 bytes), canonical vector storage (I10)."""
    return np.ascontiguousarray(emb, dtype="<f4").tobytes()


_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)


def _fts_query(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression: quote each term, OR them (recall-first).

    Quoting each token as a string literal neutralizes FTS5 operators/special chars,
    so an arbitrary user query can never raise a malformed-MATCH error.
    """
    terms = _FTS_TOKEN.findall(query.lower())
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms)


def _where_clauses(
    scope: Scope,
    statuses: list[StatusLiteral],
    as_of: datetime | None,
    *,
    alias: str,
) -> tuple[str, list[Any]]:
    """Shared scope + status + bi-temporal filter for knn/bm25.

    Always excludes quarantined notes (the default search FILTER = ``status active AND
    NOT quarantined``, G2) and enforces the cross-scope leak guard via ``user_id``.
    """
    clauses = [f"{alias}.user_id = ?", f"{alias}.quarantined = 0"]
    params: list[Any] = [scope.user_id]
    if scope.agent_id is not None:
        clauses.append(f"{alias}.agent_id = ?")
        params.append(scope.agent_id)
    if scope.session_id is not None:
        clauses.append(f"{alias}.session_id = ?")
        params.append(scope.session_id)
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        clauses.append(f"{alias}.status IN ({placeholders})")
        params.extend(statuses)
    if as_of is not None:
        iso = _to_iso(as_of)
        clauses.append(f"({alias}.valid_at IS NULL OR {alias}.valid_at <= ?)")
        clauses.append(f"({alias}.invalid_at IS NULL OR {alias}.invalid_at > ?)")
        params.extend([iso, iso])
    return " AND ".join(clauses), params


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
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)  # e.g. ~/.cold-frame on first run
        # check_same_thread=False: the MCP async seam runs sync Store calls in anyio worker
        # threads (I4). Access stays serialized (sequential tool calls + BEGIN IMMEDIATE +
        # busy_timeout); per-thread connection pooling is the P3 concurrency step (§3.2).
        conn = sqlite3.connect(
            db_path,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT}")
        conn.execute("PRAGMA secure_delete=ON")
        conn.row_factory = sqlite3.Row
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

    def _current_embedder_id(self) -> str:
        if self._embedder is not None:
            return self._embedder.meta.embedder_id
        eid = self.get_meta("embedder_id")
        if eid is None:
            raise StoreError("no embedder configured and no embedder_id in meta")
        return eid

    def _next_hlc(self) -> str:
        """Monotonic Hybrid Logical Clock string; advanced + persisted in the same txn."""
        device_id = self.get_meta("device_id") or ""
        last = self.get_meta("hlc_last") or f"0:0:{device_id}"
        last_ms_s, last_c_s, _dev = last.split(":", 2)
        now_ms = int(self._clock.now().timestamp() * 1000)
        last_ms, last_c = int(last_ms_s), int(last_c_s)
        ms, c = (now_ms, 0) if now_ms > last_ms else (last_ms, last_c + 1)
        hlc = f"{ms}:{c}:{device_id}"
        self.set_meta("hlc_last", hlc)
        return hlc

    # ── atomic write (ALL grains in one txn, I3) ────────────────────────────
    def add_note(self, note: Note, emb: np.ndarray | None) -> None:
        try:
            with self.in_transaction():
                rowid = self._insert_note_row(note)
                self._insert_fts(rowid, note)
                if emb is not None:
                    self._insert_vec(note.id, emb)
                self._insert_sources(note)
                self._insert_history(note, update_type="extract")
                self._co_write_event(note, op="create")
        except StoreError:
            raise
        except Exception as exc:  # any mid-txn failure → rolled back by in_transaction
            raise StoreError(f"add_note({note.id}) failed: {exc}") from exc

    def _insert_note_row(self, note: Note) -> int:
        cur = self._conn.execute(
            "INSERT INTO notes("
            " id, content, memory_type, keywords, tags, context, confidence, importance,"
            " user_id, agent_id, session_id, status, version, held_for_human, quarantined,"
            " triage_reason, pinned, redaction, created_at, expired_at, valid_at, invalid_at,"
            " last_accessed, access_count, decay_S, content_hash, embedder_id"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                note.id,
                note.content,
                note.memory_type,
                json.dumps(note.keywords),
                json.dumps(note.tags),
                note.context,
                note.confidence,
                note.importance,
                note.scope.user_id,
                note.scope.agent_id,
                note.scope.session_id,
                note.status,
                note.version,
                int(note.held_for_human),
                int(note.quarantined),
                note.triage_reason,
                int(note.pinned),
                None,  # redaction (P1: secrets are BLOCKed pre-disk, never stored)
                _to_iso(note.created_at),
                _opt_iso(note.expired_at),
                _opt_iso(note.valid_at),
                _opt_iso(note.invalid_at),
                _opt_iso(note.last_accessed),
                note.access_count,
                note.decay_S,
                _content_hash(note.content),
                self._current_embedder_id(),
            ),
        )
        return int(cur.lastrowid or 0)

    def _insert_fts(self, rowid: int, note: Note) -> None:
        # external-content FTS5 does not auto-sync — write the index explicitly (I10).
        self._conn.execute(
            "INSERT INTO note_fts(rowid, content, keywords, tags) VALUES (?,?,?,?)",
            (rowid, note.content, json.dumps(note.keywords), json.dumps(note.tags)),
        )

    def _insert_vec(self, note_id: str, emb: np.ndarray) -> None:
        self._conn.execute(
            "INSERT INTO note_vec(note_id, embedder_id, dim, embedding) VALUES (?,?,?,?)",
            (note_id, self._current_embedder_id(), int(emb.shape[0]), _vec_to_blob(emb)),
        )

    def _insert_sources(self, note: Note) -> None:
        extracted_at = _to_iso(note.created_at)
        self._conn.executemany(
            "INSERT INTO sources("
            " note_id, kind, ref, role, content_hash, extractor, extracted_at, observed_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    note.id,
                    s.kind,
                    s.ref,
                    s.role,
                    s.content_hash,
                    _DEFAULT_EXTRACTOR,
                    extracted_at,
                    _to_iso(s.observed_at),
                )
                for s in note.sources
            ],
        )

    def _insert_history(self, note: Note, *, update_type: UpdateType) -> None:
        self._conn.execute(
            "INSERT INTO note_history(id, version, snapshot, update_type, changed_at) "
            "VALUES (?,?,?,?,?)",
            (note.id, note.version, note.model_dump_json(), update_type, _to_iso(note.created_at)),
        )

    def _co_write_event(self, note: Note, *, op: Literal["create", "update", "archive"]) -> None:
        ev = Event(
            event_id=self._new_id(),
            device_id=self.get_meta("device_id") or "",
            hlc=self._next_hlc(),
            entity="note",
            entity_id=note.id,
            op=op,
            content_hash=_content_hash(note.content),
            payload=note.model_dump_json(),
            ts=note.created_at,
        )
        self.append_event(ev)

    def update_note(
        self, note: Note, *, update_type: UpdateType, emb: np.ndarray | None = None
    ) -> None:
        raise NotImplementedError

    def supersede(self, old_id: str, new: Note, emb: np.ndarray | None) -> None:
        existing = self.get_notes([old_id])
        if not existing:
            raise NoteNotFound(old_id)
        old = existing[0]
        try:
            with self.in_transaction():
                now = self._clock.now()
                # archive old: valid-time end = new.valid_at, transaction-time end = now (C3);
                # version++ (the archival is a new version of old). status='archived' so the
                # provenance trigger (fires only on →active) does not block this.
                self._conn.execute(
                    "UPDATE notes SET status='archived', invalid_at=?, expired_at=?, "
                    "version=version+1 WHERE id=?",
                    (_opt_iso(new.valid_at), _to_iso(now), old_id),
                )
                archived = old.model_copy(
                    update={
                        "status": "archived",
                        "invalid_at": new.valid_at,
                        "expired_at": now,
                        "version": old.version + 1,
                    }
                )
                self._conn.execute(
                    "INSERT INTO note_history(id, version, snapshot, update_type, changed_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        old_id,
                        archived.version,
                        archived.model_dump_json(),
                        "conflict",
                        _to_iso(now),
                    ),
                )
                # insert the new active note (same grains as add_note)
                rowid = self._insert_note_row(new)
                self._insert_fts(rowid, new)
                if emb is not None:
                    self._insert_vec(new.id, emb)
                self._insert_sources(new)
                self._insert_history(new, update_type="conflict")
                # supersedes edge new -> old + co-written events (archive old, create new)
                self._insert_edge(
                    Edge(
                        src_id=new.id,
                        dst_id=old_id,
                        relation="supersedes",
                        created_at=now,
                        valid_at=new.valid_at,
                    )
                )
                self._co_write_event(archived, op="archive")
                self._co_write_event(new, op="create")
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(f"supersede({old_id}) failed: {exc}") from exc

    def get_notes(self, ids: list[str]) -> list[Note]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM notes WHERE id IN ({placeholders})", ids
        ).fetchall()
        by_id = {str(r["id"]): r for r in rows}
        sources = self._load_sources(list(by_id))
        out: list[Note] = []
        for nid in ids:  # preserve requested order; unknown ids skipped silently
            row = by_id.get(nid)
            if row is not None:
                out.append(self._row_to_note(row, sources.get(nid, [])))
        return out

    def _load_sources(self, note_ids: list[str]) -> dict[str, list[Source]]:
        if not note_ids:
            return {}
        placeholders = ",".join("?" * len(note_ids))
        rows = self._conn.execute(
            f"SELECT note_id, kind, ref, role, content_hash, observed_at "
            f"FROM sources WHERE note_id IN ({placeholders})",
            note_ids,
        ).fetchall()
        out: dict[str, list[Source]] = defaultdict(list)
        for r in rows:
            out[str(r["note_id"])].append(
                Source(
                    kind=r["kind"],
                    ref=r["ref"],
                    role=r["role"],
                    content_hash=r["content_hash"],
                    observed_at=_from_iso(r["observed_at"]),
                )
            )
        return out

    @staticmethod
    def _row_to_note(row: sqlite3.Row, sources: list[Source]) -> Note:
        return Note(
            id=row["id"],
            content=row["content"],
            memory_type=row["memory_type"],
            keywords=json.loads(row["keywords"]),
            tags=json.loads(row["tags"]),
            context=row["context"],
            confidence=row["confidence"],
            scope=Scope(
                user_id=row["user_id"], agent_id=row["agent_id"], session_id=row["session_id"]
            ),
            sources=sources,
            status=row["status"],
            version=row["version"],
            created_at=_from_iso(row["created_at"]),
            expired_at=_opt_from_iso(row["expired_at"]),
            valid_at=_opt_from_iso(row["valid_at"]),
            invalid_at=_opt_from_iso(row["invalid_at"]),
            importance=row["importance"],
            last_accessed=_opt_from_iso(row["last_accessed"]),
            access_count=row["access_count"],
            decay_S=row["decay_S"],
            held_for_human=bool(row["held_for_human"]),
            quarantined=bool(row["quarantined"]),
            triage_reason=row["triage_reason"],
            pinned=bool(row["pinned"]),
        )

    def set_status(
        self, id: str, status: StatusLiteral, *, invalid_at: datetime | None = None
    ) -> None:
        try:
            with self.in_transaction():
                if invalid_at is not None:
                    self._conn.execute(
                        "UPDATE notes SET status=?, invalid_at=? WHERE id=?",
                        (status, _to_iso(invalid_at), id),
                    )
                else:
                    self._conn.execute("UPDATE notes SET status=? WHERE id=?", (status, id))
        except sqlite3.Error as exc:
            raise StoreError(f"set_status({id}) failed: {exc}") from exc

    # ── retrieval ───────────────────────────────────────────────────────────
    def knn(
        self,
        emb: np.ndarray,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        if k <= 0:
            return []
        where_sql, where_params = _where_clauses(scope, statuses, as_of, alias="n")
        # Hard-filter the current embedder (I10): mixed-dim vectors are never compared.
        rows = self._conn.execute(
            f"SELECT n.id AS id, v.embedding AS embedding "
            f"FROM note_vec v JOIN notes n ON n.id = v.note_id "
            f"WHERE v.embedder_id = ? AND {where_sql} "
            f"ORDER BY n.created_at, n.id",
            [self._current_embedder_id(), *where_params],
        ).fetchall()
        if not rows:
            return []
        ids = [str(r["id"]) for r in rows]
        mat = np.vstack([np.frombuffer(r["embedding"], dtype="<f4") for r in rows])
        q = np.asarray(emb, dtype="<f4")
        norm = float(np.linalg.norm(q))
        q = q / norm if norm > 0.0 else q
        sims = np.clip(mat @ q, -1.0, 1.0)
        order = np.argsort(-sims, kind="stable")  # desc; stable → deterministic ties
        # Exclude exact-orthogonal (cosine 0 = no shared signal) so a no-match query
        # returns []; HashEmbedder buckets are disjoint per token → 0 is genuine.
        hits = [(ids[i], float(sims[i])) for i in order if sims[i] > 0.0]
        return hits[:k]

    def bm25(
        self,
        query: str,
        k: int,
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[tuple[str, float]]:
        if k <= 0:
            return []
        match = _fts_query(query)
        if match is None:
            return []
        where_sql, where_params = _where_clauses(scope, statuses, as_of, alias="n")
        # FTS5 bm25() ranks best-match-first as ascending (more negative = better).
        rows = self._conn.execute(
            f"SELECT n.id AS id, bm25(note_fts) AS score "
            f"FROM note_fts JOIN notes n ON n.rowid = note_fts.rowid "
            f"WHERE note_fts MATCH ? AND {where_sql} "
            f"ORDER BY score LIMIT ?",
            [match, *where_params, k],
        ).fetchall()
        return [(str(r["id"]), float(r["score"])) for r in rows]

    def reinforce(self, ids: list[str], *, now: datetime) -> None:
        if not ids:
            return
        now_iso = _to_iso(now)
        try:
            with self.in_transaction():
                for nid in ids:
                    self._conn.execute(
                        "UPDATE notes SET access_count = access_count + 1, last_accessed = ?, "
                        "decay_S = MIN(decay_S + ?, ?) WHERE id = ?",
                        (now_iso, REINFORCE_DECAY_INC, DECAY_S_CAP, nid),
                    )
                    self._conn.execute(
                        "INSERT INTO access_log(note_id, ts, kind) VALUES (?, ?, 'search')",
                        (nid, now_iso),
                    )
                    # per-note cap (I13): keep the most-recently-inserted rows (rowid breaks
                    # ts ties under a frozen clock).
                    self._conn.execute(
                        "DELETE FROM access_log WHERE note_id = ? AND rowid NOT IN "
                        "(SELECT rowid FROM access_log WHERE note_id = ? ORDER BY rowid DESC "
                        "LIMIT ?)",
                        (nid, nid, ACCESS_LOG_CAP_PER_NOTE),
                    )
        except sqlite3.Error as exc:
            raise StoreError(f"reinforce failed: {exc}") from exc

    # ── edges ─────────────────────────────────────────────────────────────
    def _insert_edge(self, edge: Edge) -> None:
        # upsert (no INSERT OR REPLACE, I8); co-written inside supersede's txn or add_edge's.
        self._conn.execute(
            "INSERT INTO edges(src_id, dst_id, relation, weight, created_at, valid_at, invalid_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(src_id, dst_id, relation) DO UPDATE SET "
            "weight=excluded.weight, valid_at=excluded.valid_at, invalid_at=excluded.invalid_at",
            (
                edge.src_id,
                edge.dst_id,
                edge.relation,
                edge.weight,
                _to_iso(edge.created_at),
                _opt_iso(edge.valid_at),
                _opt_iso(edge.invalid_at),
            ),
        )

    def add_edge(self, edge: Edge) -> None:
        try:
            with self.in_transaction():
                self._insert_edge(edge)
        except sqlite3.Error as exc:
            raise StoreError(f"add_edge failed: {exc}") from exc

    def neighbors(
        self, ids: list[str], *, relations: list[EdgeRelation] | None = None
    ) -> list[Edge]:
        if not ids:
            return []
        id_ph = ",".join("?" * len(ids))
        sql = (
            f"SELECT src_id, dst_id, relation, weight, created_at, valid_at, invalid_at "
            f"FROM edges WHERE (src_id IN ({id_ph}) OR dst_id IN ({id_ph}))"
        )
        params: list[Any] = [*ids, *ids]
        if relations:
            rel_ph = ",".join("?" * len(relations))
            sql += f" AND relation IN ({rel_ph})"
            params.extend(relations)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Edge(
                src_id=r["src_id"],
                dst_id=r["dst_id"],
                relation=r["relation"],
                weight=r["weight"],
                created_at=_from_iso(r["created_at"]),
                valid_at=_opt_from_iso(r["valid_at"]),
                invalid_at=_opt_from_iso(r["invalid_at"]),
            )
            for r in rows
        ]

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
        # Co-write only: called INSIDE add_note/update_note/supersede's txn, never alone.
        self._conn.execute(
            "INSERT INTO events("
            " event_id, device_id, hlc, entity, entity_id, op, content_hash, payload, ts"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ev.event_id,
                ev.device_id,
                ev.hlc,
                ev.entity,
                ev.entity_id,
                ev.op,
                ev.content_hash,
                ev.payload,
                _to_iso(ev.ts),
            ),
        )

    def iter_events(self, *, since_hlc: str | None = None) -> Iterator[Event]:
        raise NotImplementedError

    # ── secret hard-purge ───────────────────────────────────────────────────
    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        raise NotImplementedError

    # ── housekeeping ─────────────────────────────────────────────────────────
    def doctor(self) -> dict[str, Any]:
        """Invariant snapshot for ``cold-frame doctor`` (I10 + integrity_check)."""
        notes = int(self._conn.execute("SELECT count(*) FROM notes").fetchone()[0])
        fts = int(self._conn.execute("SELECT count(*) FROM note_fts").fetchone()[0])
        vec = int(self._conn.execute("SELECT count(*) FROM note_vec").fetchone()[0])
        integrity = str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])
        meta = self.embedder_meta()
        return {
            "db_path": self._db_path,
            "notes": notes,
            "fts": fts,
            "vec": vec,
            "counts_match": notes == fts == vec,  # I10: notes==fts==vec
            "integrity": integrity,
            "embedder_id": meta.embedder_id if meta else None,
            "dim": meta.dim if meta else None,
        }

    def close(self) -> None:
        self._conn.close()
