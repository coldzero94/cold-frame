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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np

from cold_frame.constants import (
    ACCESS_LOG_CAP_PER_NOTE,
    BUSY_TIMEOUT_MS,
    CONFIDENCE_FLOOR,
    DECAY_S_CAP,
    EMBED_METRIC,
    LEASE_TTL,
    MAX_ATTEMPTS,
    REINFORCE_DECAY_INC,
    RETRY_BACKOFF_BASE,
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
from cold_frame.observability import get_logger
from cold_frame.store._ddl import DDL_V1
from cold_frame.store.base import Event, Job, PurgeReport, Store

_log = get_logger(__name__)

# Default provenance stamp for the DB ``sources.extractor`` column (data-layer §1).
# The pydantic ``Source`` model carries no ``extractor`` field (code wins), so the
# adapter supplies this storage-internal value at write time.
_DEFAULT_EXTRACTOR: str = "pipeline:v1"


def _to_iso(dt: datetime) -> str:
    """tz-aware datetime -> ISO8601-UTC TEXT with a ``Z`` suffix (I8).

    ``timespec="microseconds"`` forces a FIXED-WIDTH fractional field: without it, a whole-second
    instant serializes to ``...00Z`` while a sub-second one is ``...00.500000Z``, and since ``.`` <
    ``Z`` the later instant sorts BEFORE the earlier as TEXT — silently inverting the bi-temporal
    ``valid_at<=?`` / ``invalid_at>?`` gates and ``ORDER BY`` tiebreaks that compare these strings.
    """
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def _scope_predicate(scope: Scope) -> tuple[list[str], list[Any]]:
    """Cross-scope leak guard for unqualified-column reads (held_for_human / by_status):
    ``user_id`` always; ``agent_id`` / ``session_id`` only when the scope pins them. The one
    home for the leak-guard shape; ``_where_clauses`` builds the alias-qualified search variant.
    """
    clauses, params = ["user_id = ?"], [scope.user_id]
    for col, val in (("agent_id", scope.agent_id), ("session_id", scope.session_id)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    return clauses, params


def _where_clauses(
    scope: Scope,
    statuses: list[StatusLiteral],
    as_of: datetime | None,
    now: datetime,
    *,
    alias: str,
) -> tuple[str, list[Any]]:
    """Shared scope + status + bi-temporal filter for knn/bm25.

    Always excludes quarantined notes (the default search FILTER = ``status active AND
    NOT quarantined``, G2) and enforces the cross-scope leak guard via ``user_id``.
    With ``as_of`` → TRUE predicate (``valid_at<=as_of<invalid_at``); without ``as_of`` →
    default "currently valid + not expired" so a since-invalidated note never leaks (§5).
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
    elif "archived" not in statuses:
        # default "currently valid + not expired" so a since-invalidated note never leaks (§5). BUT
        # when the caller explicitly asked for archived rows (include_archived, no as_of), do NOT
        # apply these in-effect gates — archived notes always have expired_at in the past, so the
        # gate would nullify the inclusion and silently return nothing.
        now_iso = _to_iso(now)
        clauses.append(f"({alias}.invalid_at IS NULL OR {alias}.invalid_at > ?)")
        clauses.append(f"({alias}.expired_at IS NULL OR {alias}.expired_at > ?)")
        params.extend([now_iso, now_iso])
    return " AND ".join(clauses), params


# (target_version, ddl), append-only + in order; each step is idempotent. DDL lives in _ddl.py.
_MIGRATIONS: list[tuple[int, str]] = [(1, DDL_V1)]
# Tie the migration list to the frozen schema version (constants.py is the SoT):
# bumping SCHEMA_VERSION without appending a migration fails fast here.
assert _MIGRATIONS[-1][0] == SCHEMA_VERSION, "migrations must reach SCHEMA_VERSION"


# SQLCipher (the [crypto] extra) raises its OWN exception classes, NOT sqlite3's — a narrow
# `except sqlite3.X` would miss them in encrypted mode. These tuples catch both driver families.
try:
    import sqlcipher3.dbapi2 as _sqlcipher_dbapi  # type: ignore[import-not-found]

    _DB_ERROR: tuple[type[Exception], ...] = (sqlite3.Error, _sqlcipher_dbapi.Error)
    _DB_OPERATIONAL: tuple[type[Exception], ...] = (
        sqlite3.OperationalError,
        _sqlcipher_dbapi.OperationalError,
    )
except ImportError:  # [crypto] not installed → plaintext only, stdlib exceptions suffice
    _DB_ERROR = (sqlite3.Error,)
    _DB_OPERATIONAL = (sqlite3.OperationalError,)


def _connect(
    db_path: str,
    key: str | None,
    *,
    timeout: float = BUSY_TIMEOUT_MS / 1000,
    isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = None,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a DB connection. With ``key`` (at-rest encryption, opt-in): via SQLCipher (the
    ``[crypto]`` extra) with the key applied as the VERY FIRST statement — SQLCipher requires the
    key before any other access, and it transparently encrypts the main db + WAL + temp files.
    Without a key: stdlib sqlite3 (the default, unchanged). The key is never logged (I16)."""
    if not key:
        conn = sqlite3.connect(
            db_path,
            timeout=timeout,
            isolation_level=isolation_level,
            check_same_thread=check_same_thread,
        )
        conn.row_factory = sqlite3.Row
        return conn
    try:
        from sqlcipher3 import dbapi2 as _sqlcipher
    except ImportError as exc:  # encryption requested but the extra isn't installed
        raise StoreError(
            "at-rest encryption needs the [crypto] extra: pip install 'cold-frame[crypto]'"
        ) from exc
    conn = _sqlcipher.connect(
        db_path,
        timeout=timeout,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
    )
    # SQLCipher's PRAGMA key takes a string LITERAL, not a bind param ("near '?': syntax error").
    # Inline it as a single-quoted literal with quotes doubled → injection-safe (a key cannot break
    # out of the literal). MUST precede every other statement on the connection.
    conn.execute("PRAGMA key = '" + key.replace("'", "''") + "'")
    conn.row_factory = _sqlcipher.Row  # driver-matched Row (sqlite3.Row rejects a sqlcipher cursor)
    return conn  # type: ignore[no-any-return]  # _sqlcipher untyped → conn is Connection-compatible


def migrate_to_encrypted(src_path: str, dst_path: str, key: str) -> None:
    """Offline plaintext→encrypted migration (SQLCipher, the ``[crypto]`` extra).

    Encryption is otherwise create-time only because the online-backup API (used by ``snapshot``)
    copies raw pages and CANNOT change encryption. This opens the PLAINTEXT ``src`` with the
    SQLCipher driver (no key → it reads plaintext) and uses ``sqlcipher_export()`` to write a fully
    re-encrypted ``dst`` (full schema + data, incl. the FTS5 shadow tables). ``dst`` must not exist;
    ``key`` must be non-blank. The source is left untouched — verify ``dst`` opens with the key,
    THEN swap it in. The key is never logged (I16).
    """
    if not key or not key.strip():
        raise StoreError("encryption key must not be blank (a blank key would fail open)")
    if Path(dst_path).exists():
        raise StoreError(f"destination already exists, refusing to overwrite: {dst_path}")
    if not Path(src_path).exists():
        raise StoreError(f"source database not found: {src_path}")
    try:
        from sqlcipher3 import dbapi2 as _sqlcipher
    except ImportError as exc:
        raise StoreError(
            "at-rest encryption needs the [crypto] extra: pip install 'cold-frame[crypto]'"
        ) from exc
    src = _sqlcipher.connect(src_path)  # no PRAGMA key → SQLCipher reads the plaintext DB as-is
    try:
        # ATTACH's KEY takes a string LITERAL (like PRAGMA key), not a bind param; the path binds
        # fine. Quotes doubled → injection-safe (a key cannot break out of the literal).
        src.execute(
            "ATTACH DATABASE ? AS encrypted KEY '" + key.replace("'", "''") + "'", (dst_path,)
        )
        src.execute("SELECT sqlcipher_export('encrypted')")
        src.execute("DETACH DATABASE encrypted")
        src.commit()
    finally:
        src.close()


class SQLiteStore(Store):
    """Single-file SQLite adapter (one ``.db``: notes + FTS + vectors + edges + jobs)."""

    def __init__(
        self,
        db_path: str,
        *,
        embedder: Embedder | None = None,
        clock: Clock | None = None,
        new_id: Callable[[], str] | None = None,
        encryption_key: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._clock: Clock = clock or SystemClock()
        self._new_id: Callable[[], str] = new_id or (lambda: uuid.uuid4().hex)
        self._key = encryption_key  # opt-in at-rest encryption (SQLCipher via [crypto]); None = off
        self._conn = self._open(db_path)

    # ── connection / PRAGMAs (data-layer §3.1) ──────────────────────────────
    def _open(self, db_path: str) -> sqlite3.Connection:
        # isolation_level=None → autocommit; transactions are explicit BEGIN IMMEDIATE (I3).
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)  # e.g. ~/.cold-frame on first run
        # check_same_thread=False: the MCP async seam runs sync Store calls in anyio worker
        # threads (I4). Access stays serialized (sequential tool calls + BEGIN IMMEDIATE +
        # busy_timeout); per-thread connection pooling is the P3 concurrency step (§3.2).
        # _connect applies the SQLCipher key FIRST when at-rest encryption is on (else stdlib).
        conn = _connect(
            db_path,
            self._key,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        try:  # the first REAL access — a wrong/absent key on an encrypted DB fails HERE
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT}")
            conn.execute("PRAGMA secure_delete=ON")
        except _DB_ERROR:
            # raise a typed, KEY-FREE error (never echo the key/exc detail that could include it)
            # instead of a raw "file is not a database" that invites destructive "recovery" of a
            # perfectly healthy encrypted DB opened with the wrong key.
            hint = (
                "wrong encryption key, or this is not a cold-frame database"
                if self._key
                else "not a valid cold-frame database (corrupt, or encrypted but opened unkeyed)"
            )
            raise StoreError(f"cannot open database: {hint}") from None
        return conn  # row_factory (driver-matched) is set inside _connect

    # ── lifecycle ──────────────────────────────────────────────────────────
    def migrate(self) -> None:
        try:
            current = self._schema_version()
            target = _MIGRATIONS[-1][0]
            if 0 < current < target:  # real upgrade (not a fresh install) → snapshot first
                self._backup_before_upgrade(current)
            for version, ddl in _MIGRATIONS:
                if version <= current:
                    continue
                self._conn.executescript(ddl)  # idempotent (IF NOT EXISTS)
                self.set_meta("schema_version", str(version))
                self._conn.execute(f"PRAGMA user_version = {version}")
            self._seed_meta_once()
        except _DB_ERROR as exc:  # pragma: no cover - exercised via rollback tests later
            raise StoreError(f"migrate failed: {exc}") from exc

    def _backup_before_upgrade(self, current: int) -> None:
        """Snapshot the DB to ``<db>.bak.<current>`` before an upgrade (data-layer §4) so a
        failed migration never destroys the user's only copy. Consistent online backup (incl.
        WAL); skipped for in-memory DBs. A backup failure aborts the migration (fail-safe)."""
        if self._db_path == ":memory:":
            return
        # keyed too (else an encrypted DB's backup would be written in plaintext — a leak)
        dst = _connect(f"{self._db_path}.bak.{current}", self._key)
        try:
            self._conn.backup(dst)
        finally:
            dst.close()

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

    def stale_vector_notes(self, *, current_id: str) -> list[Note]:
        rows = self._conn.execute(
            "SELECT n.id AS id FROM notes n JOIN note_vec v ON v.note_id = n.id "
            "WHERE v.embedder_id != ? ORDER BY n.created_at, n.id",
            (current_id,),
        ).fetchall()
        return self.get_notes([str(r["id"]) for r in rows])

    def reembed(self, items: list[tuple[str, np.ndarray]], *, meta: EmbedderMeta) -> int:
        # vec retag + notes.embedder_id + the embedder_meta flip co-commit in ONE txn (I3): a
        # crash can't leave the stored meta lagging the retagged vectors. Empty items is valid —
        # it just fast-forwards the stored meta to ``meta`` (a same-id re-sync, no rewrite).
        with self._txn(f"reembed({len(items)} notes)"):
            for note_id, emb in items:
                self._conn.execute("DELETE FROM note_vec WHERE note_id=?", (note_id,))
                self._insert_vec(note_id, emb, embedder_id=meta.embedder_id)
                self._conn.execute(
                    "UPDATE notes SET embedder_id=? WHERE id=?", (meta.embedder_id, note_id)
                )
            self.set_embedder_meta(meta)  # same txn → atomic with the retag
        return len(items)

    def get_meta(self, key: str) -> str | None:
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        except _DB_OPERATIONAL:
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

    @contextmanager
    def _txn(self, op: str) -> Iterator[None]:
        """BEGIN IMMEDIATE…COMMIT (I3) with uniform error translation: any failure inside rolls
        back; a DB/logic error surfaces as ``StoreError(f"{op} failed: …")`` while a StoreError or
        NoteNotFound raised inside passes through unchanged. Replaces the per-method try/except."""
        try:
            with self.in_transaction():
                yield
        except (StoreError, NoteNotFound):
            raise
        except Exception as exc:
            raise StoreError(f"{op} failed: {exc}") from exc

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

    def _assert_provenance(self, note: Note) -> None:
        # Provenance invariant pre-commit guard (I14): an active, non-quarantined, high-confidence
        # note MUST carry >=1 source. The DB trigger only covers the UPDATE→active path; this guards
        # every INSERT path (the trigger does not fire on INSERT) — add_note AND supersede.
        if (
            note.status == "active"
            and not note.quarantined
            and note.confidence >= CONFIDENCE_FLOOR
            and not note.sources
        ):
            raise StoreError(
                f"provenance invariant (I14): active note {note.id} "
                f"(confidence {note.confidence}) needs >=1 source"
            )

    # ── atomic write (ALL grains in one txn, I3) ────────────────────────────
    def add_note(self, note: Note, emb: np.ndarray | None) -> None:
        self._assert_provenance(note)
        with self._txn(f"add_note({note.id})"):  # ONE txn: notes+fts+vec+sources+history+event (I3)
            rowid = self._insert_note_row(note)
            self._insert_fts(rowid, note)
            if emb is not None:
                self._insert_vec(note.id, emb)
            self._insert_sources(note)
            self._insert_history(note, update_type="extract")
            self._co_write_event(note, op="create")

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
        # ``tags`` are coarse structured labels (memory_type + salient terms) for display/filter,
        # NOT a full-text facet — indexing them would make the memory_type word ("semantic" etc.) a
        # matchable BM25 term and double-index content-derived terms. ``keywords`` are the search
        # facet; the tags FTS column is left empty on purpose (schema keeps it, no migration). Both
        # FTS writes MUST pass identical column values (external-content delete replays them).
        self._conn.execute(
            "INSERT INTO note_fts(rowid, content, keywords, tags) VALUES (?,?,?,?)",
            (rowid, note.content, json.dumps(note.keywords), ""),
        )

    def _delete_fts(self, rowid: int, note: Note) -> None:
        # external-content FTS5 has no auto-sync/FK-cascade — drop the OLD index row explicitly,
        # passing the exact values that were indexed (I10). Mirrors _insert_fts (tags empty).
        self._conn.execute(
            "INSERT INTO note_fts(note_fts, rowid, content, keywords, tags) "
            "VALUES ('delete', ?, ?, ?, ?)",
            (rowid, note.content, json.dumps(note.keywords), ""),
        )

    def _insert_vec(self, note_id: str, emb: np.ndarray, *, embedder_id: str | None = None) -> None:
        # ``embedder_id`` overrides the live embedder for re-embedding (the vector is tagged with
        # the embedder that produced it, not whatever is currently configured).
        eid = embedder_id or self._current_embedder_id()
        self._conn.execute(
            "INSERT INTO note_vec(note_id, embedder_id, dim, embedding) VALUES (?,?,?,?)",
            (note_id, eid, int(emb.shape[0]), _vec_to_blob(emb)),
        )

    def _insert_sources(self, note: Note) -> None:
        # NOTE: extractor/extracted_at are written (a fixed _DEFAULT_EXTRACTOR + created_at) but not
        # yet read back — RESERVED for R6 provenance versioning (the real prompt/model version, once
        # prompt-versioning lands). Not surfaced on the Source model in v1.
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

    def _insert_history(
        self, note: Note, *, update_type: UpdateType, changed_at: datetime | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO note_history(id, version, snapshot, update_type, changed_at) "
            "VALUES (?,?,?,?,?)",
            (
                note.id,
                note.version,
                note.model_dump_json(),
                update_type,
                _to_iso(changed_at or note.created_at),
            ),
        )

    def _co_write_event(
        self,
        note: Note,
        *,
        op: Literal["create", "update", "archive", "delete"],
        ts: datetime | None = None,
    ) -> None:
        # event ts = when the operation happened: `ts` for update/archive, created_at for create.
        ev = Event(
            event_id=self._new_id(),
            device_id=self.get_meta("device_id") or "",
            hlc=self._next_hlc(),
            entity="note",
            entity_id=note.id,
            op=op,
            content_hash=_content_hash(note.content),
            payload=note.model_dump_json(),
            ts=ts or note.created_at,
        )
        self.append_event(ev)

    def update_note(
        self, note: Note, *, update_type: UpdateType, emb: np.ndarray | None = None
    ) -> None:
        existing = self.get_notes([note.id])
        if not existing:
            raise NoteNotFound(note.id)
        old = existing[0]
        with self._txn(f"update_note({note.id})"):  # ONE txn: notes+fts+vec+history+event (I3)
            rowid = int(
                self._conn.execute("SELECT rowid FROM notes WHERE id=?", (note.id,)).fetchone()[0]
            )
            now = self._clock.now()
            # external-content FTS5 has no auto-sync: delete the OLD index row, insert the NEW
            self._delete_fts(rowid, old)
            cur = self._conn.execute(
                "UPDATE notes SET content=?, keywords=?, tags=?, context=?, confidence=?, "
                "importance=?, pinned=?, status=?, version=?, valid_at=?, invalid_at=?, "
                "content_hash=? WHERE id=? AND version=?",  # optimistic lock: read-time version
                (
                    note.content,
                    json.dumps(note.keywords),
                    json.dumps(note.tags),
                    note.context,
                    note.confidence,
                    note.importance,
                    int(note.pinned),  # lifecycle flag carried through metadata patch (update())
                    note.status,
                    note.version,
                    _opt_iso(note.valid_at),
                    _opt_iso(note.invalid_at),
                    _content_hash(note.content),
                    note.id,
                    note.version - 1,  # the version the caller read and intends to supersede
                ),
            )
            if cur.rowcount == 0:  # DB version moved under us → a concurrent write won
                raise StoreError(
                    f"update_note({note.id}): version conflict (expected {note.version - 1})"
                )
            self._insert_fts(rowid, note)
            if emb is not None:  # replace the vector (content may have changed)
                self._conn.execute("DELETE FROM note_vec WHERE note_id=?", (note.id,))
                self._insert_vec(note.id, emb)
            self._insert_history(note, update_type=update_type, changed_at=now)
            self._co_write_event(note, op="update", ts=now)

    def supersede(self, old_id: str, new: Note, emb: np.ndarray | None) -> None:
        existing = self.get_notes([old_id])
        if not existing:
            raise NoteNotFound(old_id)
        old = existing[0]
        self._assert_provenance(new)  # supersede mints an active note → same I14 guard as add_note
        with self._txn(f"supersede({old_id})"):
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
            self._insert_history(archived, update_type="conflict", changed_at=now)
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
            self._co_write_event(archived, op="archive", ts=now)
            self._co_write_event(new, op="create")

    def consolidate_commit(
        self,
        summary: Note,
        emb: np.ndarray | None,
        *,
        member_ids: list[str],
        demote_ids: list[str],
        factor: float,
        at: datetime,
    ) -> None:
        self._assert_provenance(summary)
        # ONE txn (I3): the summary's grains + the derived_from convergence edges + the cold-demote
        # all co-commit, so a partial failure can't orphan the summary and trigger a duplicate on
        # the next durable retry (the edges are what mark members already-consolidated).
        with self._txn(f"consolidate_commit({summary.id})"):
            rowid = self._insert_note_row(summary)
            self._insert_fts(rowid, summary)
            if emb is not None:
                self._insert_vec(summary.id, emb)
            self._insert_sources(summary)
            self._insert_history(summary, update_type="extract")
            self._co_write_event(summary, op="create")
            for mid in member_ids:
                self._insert_edge(
                    Edge(src_id=summary.id, dst_id=mid, relation="derived_from", created_at=at)
                )
            if demote_ids:
                placeholders = ",".join("?" * len(demote_ids))
                self._conn.execute(
                    f"UPDATE notes SET decay_S = decay_S * ? WHERE id IN ({placeholders})",
                    [factor, *demote_ids],
                )

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

    def get_notes_filtered(
        self,
        ids: list[str],
        *,
        scope: Scope,
        statuses: list[StatusLiteral],
        as_of: datetime | None = None,
    ) -> list[Note]:
        """``get_notes`` restricted to the SAME predicate knn/bm25 apply — scope + status +
        quarantine + bi-temporal in-effect gate — by reusing ``_where_clauses`` VERBATIM (no Python
        re-implementation, so the edge channel can't drift from the search guard). Order preserved.
        """
        if not ids:
            return []
        where_sql, where_params = _where_clauses(
            scope, statuses, as_of, self._clock.now(), alias="n"
        )
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT n.* FROM notes n WHERE n.id IN ({placeholders}) AND {where_sql}",
            [*ids, *where_params],
        ).fetchall()
        by_id = {str(r["id"]): r for r in rows}
        sources = self._load_sources(list(by_id))
        return [self._row_to_note(by_id[nid], sources.get(nid, [])) for nid in ids if nid in by_id]

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
        with self._txn(f"set_status({id})"):
            if invalid_at is not None:
                self._conn.execute(
                    "UPDATE notes SET status=?, invalid_at=? WHERE id=?",
                    (status, _to_iso(invalid_at), id),
                )
            else:
                self._conn.execute("UPDATE notes SET status=? WHERE id=?", (status, id))

    def set_pinned(self, id: str, pinned: bool) -> None:
        """Set the pin flag (pinned notes are exempt from decay/archive, I13)."""
        with self._txn(f"set_pinned({id})"):
            self._conn.execute("UPDATE notes SET pinned=? WHERE id=?", (int(pinned), id))

    def archive(self, id: str, *, now: datetime) -> None:
        existing = self.get_notes([id])
        if not existing:
            raise NoteNotFound(id)
        old = existing[0]
        with self._txn(f"archive({id})"):
            # transaction-time end only (expired_at=now); valid-time is unchanged — the
            # fact isn't false, we just stopped keeping it (distinct from supersede).
            self._conn.execute(
                "UPDATE notes SET status='archived', expired_at=?, version=version+1 WHERE id=?",
                (_to_iso(now), id),
            )
            snap = old.model_copy(
                update={"status": "archived", "expired_at": now, "version": old.version + 1}
            )
            self._insert_history(snap, update_type="consolidate", changed_at=now)
            self._co_write_event(snap, op="archive", ts=now)  # I3/I17: archive grain

    def revive(self, id: str) -> None:
        existing = self.get_notes([id])
        if not existing:
            raise NoteNotFound(id)
        old = existing[0]
        now = self._clock.now()
        with self._txn(f"revive({id})"):
            # un-archive: clear both temporal ends so a revived note is current again (I2)
            self._conn.execute(
                "UPDATE notes SET status='active', invalid_at=NULL, expired_at=NULL, "
                "version=version+1 WHERE id=?",
                (id,),
            )
            snap = old.model_copy(
                update={
                    "status": "active",
                    "invalid_at": None,
                    "expired_at": None,
                    "version": old.version + 1,
                }
            )
            self._insert_history(snap, update_type="manual", changed_at=now)
            self._co_write_event(snap, op="update", ts=now)

    def delete(self, id: str) -> None:
        existing = self.get_notes([id])
        if not existing:
            raise NoteNotFound(id)
        old = existing[0]
        now = self._clock.now()
        with self._txn(f"delete({id})"):
            rowid = int(
                self._conn.execute("SELECT rowid FROM notes WHERE id=?", (id,)).fetchone()[0]
            )
            # external-content FTS5 has no FK cascade: drop the index entry explicitly
            self._delete_fts(rowid, old)
            self._co_write_event(old, op="delete", ts=now)  # audit the removal (payload kept)
            self._conn.execute("DELETE FROM note_history WHERE id=?", (id,))  # no FK cascade
            # DELETE notes cascades note_vec / edges / sources / access_log (ON DELETE CASCADE)
            self._conn.execute("DELETE FROM notes WHERE id=?", (id,))

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
        where_sql, where_params = _where_clauses(
            scope, statuses, as_of, self._clock.now(), alias="n"
        )
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
        where_sql, where_params = _where_clauses(
            scope, statuses, as_of, self._clock.now(), alias="n"
        )
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
        with self._txn("reinforce"):  # same error-translating wrapper as every other mutator
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
        with self._txn("add_edge"):  # same error-translating wrapper as every other mutator
            self._insert_edge(edge)

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
        clauses, params = _scope_predicate(scope)
        clauses += ["status = 'active'", "held_for_human = 1"]  # active held notes only
        rows = self._conn.execute(
            f"SELECT * FROM notes WHERE {' AND '.join(clauses)} "
            "ORDER BY importance DESC, id LIMIT ?",
            [*params, limit],
        ).fetchall()
        sources = self._load_sources([str(r["id"]) for r in rows])
        return [self._row_to_note(r, sources.get(str(r["id"]), [])) for r in rows]

    def set_held_for_human(
        self, id: str, *, held: bool, quarantined: bool, reason: str | None
    ) -> None:
        with self._txn(f"set_held_for_human({id})"):
            self._conn.execute(
                "UPDATE notes SET held_for_human=?, quarantined=?, triage_reason=? WHERE id=?",
                (int(held), int(quarantined), reason, id),
            )

    def by_status(
        self,
        *,
        scope: Scope,
        status: StatusLiteral,
        sort: Literal["decay", "recent", "importance"],
        limit: int,
        offset: int = 0,
    ) -> list[Note]:
        order = {
            "recent": "created_at DESC",
            "importance": "importance DESC",
            "decay": "decay_S ASC",  # least-stable (most-decayed) first
        }[sort]
        clauses, params = _scope_predicate(scope)
        clauses.append("status = ?")
        params.append(status)
        rows = self._conn.execute(
            f"SELECT * FROM notes WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order}, id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        sources = self._load_sources([str(r["id"]) for r in rows])
        return [self._row_to_note(r, sources.get(str(r["id"]), [])) for r in rows]

    def find_procedural(self, name: str, scope: Scope) -> Note | None:
        # targeted EXACT-scope lookup (NULL-safe `IS ?` on agent_id/session_id) so a directive can
        # never be missed past a recency page, nor a broad scope bleed in a narrower one's note.
        row = self._conn.execute(
            "SELECT * FROM notes WHERE user_id=? AND agent_id IS ? AND session_id IS ? "
            "AND memory_type='procedural' AND context=? AND status='active' "
            "ORDER BY created_at DESC, id LIMIT 1",
            (scope.user_id, scope.agent_id, scope.session_id, name),
        ).fetchone()
        if row is None:
            return None
        sources = self._load_sources([str(row["id"])])
        return self._row_to_note(row, sources.get(str(row["id"]), []))

    def get_history(self, id: str) -> list[Note]:
        """All persisted versions of ``id`` (oldest→newest), reconstructed from note_history."""
        rows = self._conn.execute(
            "SELECT snapshot FROM note_history WHERE id=? ORDER BY version", (id,)
        ).fetchall()
        return [Note.model_validate_json(r["snapshot"]) for r in rows]

    def access_log(self, id: str, *, limit: int = 50) -> list[datetime]:
        """Recall timestamps for ``id`` (oldest→newest); table is capped at 50 rows/note (I13)."""
        rows = self._conn.execute(
            "SELECT ts FROM access_log WHERE note_id=? ORDER BY ts LIMIT ?", (id, limit)
        ).fetchall()
        return [_from_iso(r["ts"]) for r in rows]

    def as_of(self, ids: list[str], *, at: datetime) -> list[Note]:
        # Bi-temporal valid-time read (ABC: valid_at<=at<invalid_at). Pick the highest version
        # whose valid_at<=at (the latest correction that had taken effect by `at`), then include
        # it only if it is still valid then. The invalid_at gate MUST apply to the chosen version,
        # not each snapshot: an early snapshot was frozen with invalid_at=None before a later
        # supersede set it, so per-snapshot gating would wrongly resurrect an invalidated note.
        out: list[Note] = []
        for nid in ids:
            chosen: Note | None = None
            for snap in self.get_history(nid):  # version-ascending → latest effective wins
                if snap.valid_at is not None and snap.valid_at <= at:
                    chosen = snap
            if chosen is not None and (chosen.invalid_at is None or at < chosen.invalid_at):
                out.append(chosen)
        return out

    # ── jobs (durable queue, I12) ───────────────────────────────────────────
    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            dedup_key=row["dedup_key"],
            run_after=_from_iso(row["run_after"]),
            locked_by=row["locked_by"],
            locked_at=_opt_from_iso(row["locked_at"]),
            last_error=row["last_error"],
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
        run_after: datetime | None = None,
    ) -> str:
        now = self._clock.now()
        ra = _to_iso(run_after or now)
        now_iso = _to_iso(now)
        with self._txn(f"enqueue({kind})"):
            if dedup_key is not None:  # debounce: one pending job per dedup_key
                row = self._conn.execute(
                    "SELECT id FROM jobs WHERE dedup_key=? AND status='pending'", (dedup_key,)
                ).fetchone()
                if row is not None:
                    existing = str(row["id"])
                    self._conn.execute(
                        "UPDATE jobs SET run_after=MIN(run_after, ?), updated_at=? WHERE id=?",
                        (ra, now_iso, existing),
                    )
                    return existing
            jid = self._new_id()
            self._conn.execute(
                "INSERT INTO jobs(id, kind, payload, status, attempts, max_attempts, "
                "dedup_key, run_after, created_at, updated_at) "
                "VALUES (?,?,?,'pending',0,?,?,?,?,?)",
                (jid, kind, json.dumps(payload), MAX_ATTEMPTS, dedup_key, ra, now_iso, now_iso),
            )
            return jid

    def lease_job(self, *, worker: str, now: datetime) -> Job | None:
        now_iso = _to_iso(now)
        stale = _to_iso(now - timedelta(seconds=LEASE_TTL))  # crashed-worker reclaim
        with self._txn("lease_job"):
            # loop: skip past any poison rows we dead-letter, so we don't falsely report empty
            while True:
                row = self._conn.execute(
                    "SELECT id, status, attempts FROM jobs "
                    "WHERE (status='pending' AND run_after<=?) "
                    "OR (status='running' AND locked_at<?) ORDER BY run_after LIMIT 1",
                    (now_iso, stale),
                ).fetchone()
                if row is None:
                    return None  # genuinely nothing leasable
                jid = str(row["id"])
                # a stale RUNNING row is a crashed worker's job. If it already used up its attempts,
                # it crashed every time (a poison job that never reached fail_job) — dead-letter it
                # (I12) and KEEP scanning; returning None here would abort the whole drain pass even
                # though other jobs are pending.
                if row["status"] == "running" and int(row["attempts"]) >= MAX_ATTEMPTS:
                    # the most severe job outcome — surface it (content-free: id + attempts only).
                    _log.error(
                        "job_dead_lettered",
                        extra={
                            "job_id": jid,
                            "attempts": int(row["attempts"]),
                            "reason": "crash_loop",
                        },
                    )
                    self._conn.execute(
                        "UPDATE jobs SET status='dead', last_error=?, updated_at=? WHERE id=?",
                        ("reclaim exhausted — worker crash loop", now_iso, jid),
                    )
                    continue  # the dead row drops out of the SELECT predicate → next row
                self._conn.execute(
                    "UPDATE jobs SET status='running', locked_by=?, locked_at=?, "
                    "attempts=attempts+1, updated_at=? WHERE id=?",
                    (worker, now_iso, now_iso, jid),
                )
                leased = self._conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
                return self._row_to_job(leased)

    def finish_job(self, id: str, *, worker: str) -> None:
        with self._txn(f"finish_job({id})"):
            cur = self._conn.execute(
                "UPDATE jobs SET status='done', updated_at=? WHERE id=? AND locked_by=?",
                (_to_iso(self._clock.now()), id, worker),
            )
            if cur.rowcount == 0:  # lease stolen (stale-reclaimed) → no-op, don't clobber
                _log.warning("finish_job_lost_lease", extra={"job_id": id, "worker": worker})

    def fail_job(self, id: str, *, error: str, retry_after: datetime | None, worker: str) -> None:
        now = self._clock.now()
        with self._txn(f"fail_job({id})"):
            row = self._conn.execute(
                "SELECT attempts, max_attempts, locked_by, dedup_key FROM jobs WHERE id=?", (id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"fail_job: job {id} not found")
            if row["locked_by"] != worker:  # lease stolen → another worker owns it now
                _log.warning("fail_job_lost_lease", extra={"job_id": id, "worker": worker})
                return
            if row["attempts"] >= row["max_attempts"]:  # exhausted → dead-letter (never dropped)
                self._conn.execute(
                    "UPDATE jobs SET status='dead', last_error=?, updated_at=? WHERE id=?",
                    (error, _to_iso(now), id),
                )
            else:  # reschedule with exponential backoff
                backoff = RETRY_BACKOFF_BASE * (2 ** row["attempts"])
                ra = retry_after or (now + timedelta(seconds=backoff))
                # if a same-key pending sibling appeared while we ran, re-pend WITHOUT the key to
                # avoid the idx_jobs_dedup collision (keeps backoff; the sibling runs, idempotent)
                dk = row["dedup_key"]
                collide = self._has_pending_dedup(dk)
                self._conn.execute(
                    "UPDATE jobs SET status='pending', run_after=?, last_error=?, dedup_key=?, "
                    "updated_at=? WHERE id=?",
                    (_to_iso(ra), error, None if collide else dk, _to_iso(now), id),
                )

    def pending_count(self, kind: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM jobs WHERE status='pending'"
        params: tuple[str, ...] = ()
        if kind is not None:
            sql += " AND kind=?"
            params = (kind,)
        row = self._conn.execute(sql, params).fetchone()
        return int(row["n"])

    def dead_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='dead'").fetchone()["n"]
        )

    def _has_pending_dedup(self, dedup_key: str | None) -> bool:
        """True if a PENDING job already holds ``dedup_key`` (the idx_jobs_dedup unique scope)."""
        if dedup_key is None:
            return False
        return (
            self._conn.execute(
                "SELECT 1 FROM jobs WHERE status='pending' AND dedup_key=? LIMIT 1", (dedup_key,)
            ).fetchone()
            is not None
        )

    def requeue_dead(self, *, now: datetime) -> int:
        iso = _to_iso(now)
        # A blanket UPDATE dead->pending crashes on idx_jobs_dedup (UNIQUE WHERE status='pending')
        # when two dead jobs share a dedup_key, OR a dead job's key matches a live pending one —
        # legitimate: enqueue() debounces only against 'pending' and dead-letter keeps the key.
        # Requeue per-row: if the key already lives on a pending row, requeue WITHOUT it so the
        # recovery still runs (idempotent handlers make the duplicate safe) instead of throwing and
        # recovering nothing. _txn so any driver error surfaces as StoreError (contract), not raw.
        with self._txn("requeue_dead"):
            dead = self._conn.execute(
                "SELECT id, dedup_key FROM jobs WHERE status='dead'"
            ).fetchall()
            for row in dead:
                dk = row["dedup_key"]
                collide = self._has_pending_dedup(dk)
                self._conn.execute(
                    "UPDATE jobs SET status='pending', attempts=0, run_after=?, locked_by=NULL, "
                    "locked_at=NULL, dedup_key=?, updated_at=? WHERE id=?",
                    (iso, None if collide else dk, iso, row["id"]),
                )
            return len(dead)

    def oldest_pending_age(self, *, now: datetime) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(created_at) AS oldest FROM jobs WHERE status='pending'"
        ).fetchone()
        oldest = row["oldest"]  # MIN() always returns one row (oldest is NULL when none pending)
        return None if not oldest else (now - _from_iso(str(oldest))).total_seconds()

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
        sql = (
            "SELECT event_id, device_id, hlc, entity, entity_id, op, content_hash, payload, ts "
            "FROM events"
        )
        params: list[Any] = []
        if since_hlc is not None:
            sql += " WHERE hlc > ?"
            params.append(since_hlc)
        sql += " ORDER BY hlc, seq"  # hlc-ordered, append order as tiebreak (deterministic)
        rows = self._conn.execute(sql, params).fetchall()  # materialize: frees the shared cursor
        for row in rows:
            yield Event(
                event_id=row["event_id"],
                device_id=row["device_id"],
                hlc=row["hlc"],
                entity=row["entity"],
                entity_id=row["entity_id"],
                op=row["op"],
                content_hash=row["content_hash"],
                payload=row["payload"],
                ts=_from_iso(row["ts"]),
            )

    def snapshot(self, dst: str) -> None:
        """Consistent checkpointed copy of the WHOLE DB to ``dst`` (I17: a snapshot, never the
        live WAL). Single-file, WAL-free — restorable by copying it back into place. An encrypted
        store produces an ENCRYPTED snapshot (the target is keyed with the same key — no leak)."""
        out = _connect(dst, self._key)
        try:
            self._conn.backup(out)
        finally:
            out.close()

    # ── secret hard-purge (the ONE append-only carve-out, I2/I17/§7) ─────────
    def _purge_targets(self, id: str, *, cascade: bool) -> list[str]:
        """The id plus, if cascade, every note derived FROM it (BFS over ``derived_from``
        edges where the target is the dst) so a secret can't be reconstructed from a summary."""
        if not self.get_notes([id]):
            raise NoteNotFound(id)
        targets, frontier, seen = [id], [id], {id}
        while cascade and frontier:
            edges = self.neighbors(frontier, relations=["derived_from"])
            nxt: list[str] = []
            for e in edges:  # mark seen AT enqueue so a multi-parent node is visited only once
                if e.dst_id in seen and e.src_id not in seen:
                    seen.add(e.src_id)
                    nxt.append(e.src_id)
                    targets.append(e.src_id)
            frontier = nxt
        return targets

    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        targets = self._purge_targets(id, cascade=cascade)
        notes = self.get_notes(targets)
        # the plaintext we must prove gone afterwards (content is the secret; context may echo it)
        needles = [s for n in notes for s in (n.content, n.context) if s]
        rows = 0
        now = self._clock.now()
        with self._txn(f"purge({id})"):  # all grain-scrubbing in ONE txn (I3)
            for n in notes:
                rowid = int(
                    self._conn.execute("SELECT rowid FROM notes WHERE id=?", (n.id,)).fetchone()[0]
                )
                # external-content FTS5 has no FK cascade: drop the indexed terms explicitly
                self._delete_fts(rowid, n)
                self._conn.execute("DELETE FROM note_history WHERE id=?", (n.id,))  # no FK cascade
                # DELETE notes cascades note_vec / edges / sources / access_log (ON DELETE CASCADE)
                rows += self._conn.execute("DELETE FROM notes WHERE id=?", (n.id,)).rowcount
                # scrub the append-only event payloads for this note — the documented carve-out:
                # the audit ROW stays (op/ts/id), but the content-bearing payload/hash is gone.
                rows += self._conn.execute(
                    "UPDATE events SET payload='', content_hash=NULL "
                    "WHERE entity='note' AND entity_id=?",
                    (n.id,),
                ).rowcount
                self._record_purge_event(n.id, ts=now)  # content-free tombstone of the purge
            # job payloads can embed content (e.g. a queued summary) — scrub any that do
            for jid, payload in self._conn.execute("SELECT id, payload FROM jobs").fetchall():
                scrubbed = payload
                for needle in needles:
                    if needle and needle in scrubbed:
                        scrubbed = scrubbed.replace(needle, "")
                if scrubbed != payload:
                    self._conn.execute("UPDATE jobs SET payload=? WHERE id=?", (scrubbed, jid))
                    rows += 1
        # compact so freed pages (zeroed by secure_delete=ON) leave no recoverable residue, then
        # flush to the main db and truncate the WAL before grep-verifying the live files (§7).
        self._conn.execute("INSERT INTO note_fts(note_fts) VALUES ('optimize')")
        self._conn.execute("VACUUM")
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return PurgeReport(
            note_id=id,
            rows_scrubbed=rows,
            grep_clean=self._grep_clean(needles),
            vacuumed=True,
        )

    def _record_purge_event(self, entity_id: str, *, ts: datetime) -> None:
        """Append a content-free ``purge`` event (audit that a scrub happened, no payload)."""
        self.append_event(
            Event(
                event_id=self._new_id(),
                device_id=self.get_meta("device_id") or "",
                hlc=self._next_hlc(),
                entity="note",
                entity_id=entity_id,
                op="purge",
                content_hash=None,
                payload="",
                ts=ts,
            )
        )

    def _grep_clean(self, needles: list[str]) -> bool:
        """True iff none of ``needles`` (plaintext) survives in the live ``.db``/``.db-wal``.
        Honest scope (§7): the live DB only — OS snapshots/backups/free-list aren't covered."""
        if self._db_path == ":memory:":
            return True  # no on-disk file → no recoverable residue (rows already gone from RAM)
        if self._key:
            # ENCRYPTED: the file is ciphertext, so a raw-byte grep would ALWAYS be "clean" for the
            # wrong reason (false confidence). Verify the LOGICAL scrub via the keyed (decrypting)
            # connection instead — the needle must be absent from every live content-bearing grain.
            return self._content_clean(needles)
        blobs = b"".join(
            Path(p).read_bytes()
            for p in (self._db_path, f"{self._db_path}-wal")
            if Path(p).exists()
        )
        return all(needle.encode("utf-8") not in blobs for needle in needles if needle)

    def _content_clean(self, needles: list[str]) -> bool:
        """True iff no ``needle`` appears in any live content-bearing column (read through the keyed
        connection, so it sees decrypted content). The meaningful purge proof under encryption."""
        # notes.content + notes.context (both free-text, both PII-redacted) + the two scrubbed JSON
        # grains. Mirrors the plaintext byte-grep's coverage so the encrypted path isn't weaker.
        targets = (
            ("notes", "content"),
            ("notes", "context"),
            ("events", "payload"),
            ("jobs", "payload"),
        )
        for needle in (n for n in needles if n):
            for table, col in targets:
                hit = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE instr({col}, ?) > 0 LIMIT 1", (needle,)
                ).fetchone()
                if hit is not None:
                    return False
        return True

    # ── housekeeping ─────────────────────────────────────────────────────────
    def doctor(self) -> dict[str, Any]:
        """Invariant snapshot for ``cold-frame doctor`` (I10 + integrity_check)."""
        notes = int(self._conn.execute("SELECT count(*) FROM notes").fetchone()[0])
        fts = int(self._conn.execute("SELECT count(*) FROM note_fts").fetchone()[0])
        vec = int(self._conn.execute("SELECT count(*) FROM note_vec").fetchone()[0])
        integrity = str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])
        meta = self.embedder_meta()
        # the LIVE embedder is the truth for what new writes use + what KNN filters on; the
        # stored meta can lag after an embedder swap (until `reembed` updates it).
        live = self._embedder.meta if self._embedder is not None else meta
        current_id = live.embedder_id if live is not None else None
        # real FTS5 integrity (the external-content row count above can't detect index drift)
        try:
            self._conn.execute("INSERT INTO note_fts(note_fts) VALUES ('integrity-check')")
            fts_integrity = "ok"
        except _DB_ERROR as exc:
            fts_integrity = f"corrupt: {exc}"
        # vectors written by a different embedder than the current one (KNN excludes them, I10) →
        # they need `reembed` to become searchable again.
        stale_vectors = 0
        if current_id is not None:
            stale_vectors = int(
                self._conn.execute(
                    "SELECT count(*) FROM note_vec WHERE embedder_id != ?", (current_id,)
                ).fetchone()[0]
            )
        return {
            "db_path": self._db_path,
            "notes": notes,
            "fts": fts,
            "vec": vec,
            "counts_match": notes == fts == vec,  # I10: notes==fts==vec
            "integrity": integrity,
            "fts_integrity": fts_integrity,
            "stale_vectors": stale_vectors,
            "embedder_id": current_id,
            "dim": live.dim if live is not None else None,
        }

    def close(self) -> None:
        self._conn.close()
