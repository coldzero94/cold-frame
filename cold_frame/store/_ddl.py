"""v1 schema DDL — the full ``migration 0 -> 1`` for SQLiteStore.

Pure data (no behavior, no ``self``, no txn semantics): kept out of ``sqlite.py`` so the
adapter file stays method-logic only. Idempotent via ``IF NOT EXISTS`` (CLAUDE.md §8:
additive, ``user_version``-gated). Imported by ``sqlite.py`` as the migration-1 statement.
"""

from __future__ import annotations

DDL_V1 = """
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
