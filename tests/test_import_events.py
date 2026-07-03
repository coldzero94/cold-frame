"""Event-log replay import (I17): idempotent, keyed on event_id, LWW-by-HLC.

`export_events` dumps the append-only note log as NDJSON; `import_events` replays it into another
store. Applying is last-writer-wins by HLC (a note's state = its highest-HLC event, local OR
imported); an already-stored event_id is skipped (idempotency). Note-only (edges aren't logged).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cold_frame.api import Memory
from cold_frame.cli import main
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Note, Scope, Source
from cold_frame.store.base import Event

from tests.conftest import FrozenClock


def _mem(path: str, clock: FrozenClock) -> Memory:
    return Memory(path, embedder=HashEmbedder(), llm=None, clock=clock)


def test_import_restores_into_empty_store(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    src = _mem(str(tmp_path / "src.db"), frozen_clock)
    src.add("I prefer dark roast coffee")
    src.add("the mitochondria is the powerhouse of the cell")
    lines = list(src.export_events())

    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    res = dst.import_events(lines)
    assert res.materialized == 2
    assert {n.content for n in dst.list_active()} == {
        "I prefer dark roast coffee",
        "the mitochondria is the powerhouse of the cell",
    }
    assert dst.search("mitochondria").hits  # FTS index rebuilt on import
    assert dst.search("coffee").hits  # vectors re-embedded on import


def test_import_is_idempotent(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    src = _mem(str(tmp_path / "src.db"), frozen_clock)
    src.add("fact one")
    src.add("fact two")
    lines = list(src.export_events())

    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    dst.import_events(lines)
    notes_after_first = len(dst.list_active())
    events_after_first = len(list(dst.export_events()))

    again = dst.import_events(lines)  # re-import the SAME log
    assert again.materialized == 0 and again.skipped == len(lines)  # every event already stored
    assert len(dst.list_active()) == notes_after_first  # no duplicate notes
    assert len(list(dst.export_events())) == events_after_first  # no duplicate events


def _note_event(m: Memory, note_id: str, *, content: str, hlc: str) -> str:
    """An NDJSON event line carrying a full note snapshot with a controlled HLC (for LWW tests)."""
    note = m.get(note_id).model_copy(update={"content": content})
    ev = Event(
        event_id=f"ext-{hlc}",
        device_id="other-device",
        hlc=hlc,
        entity="note",
        entity_id=note_id,
        op="update",
        payload=note.model_dump_json(),
        ts=m._clock.now(),
    )
    return ev.model_dump_json()


def test_import_older_event_does_not_clobber_newer_local(
    tmp_path: Path, frozen_clock: FrozenClock
) -> None:
    m = _mem(str(tmp_path / "m.db"), frozen_clock)
    nid = m.add("current local belief").added[0].id
    older = _note_event(m, nid, content="stale imported content", hlc="0000000000000:0:other")
    m.import_events([older])
    assert m.get(nid).content == "current local belief"  # local (higher HLC) wins — not clobbered


def test_import_newer_event_applies(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    m = _mem(str(tmp_path / "m.db"), frozen_clock)
    nid = m.add("old local belief").added[0].id
    newer = _note_event(m, nid, content="fresh imported belief", hlc="9999999999999:0:other")
    res = m.import_events([newer])
    assert res.materialized == 1
    assert m.get(nid).content == "fresh imported belief"  # newer imported event wins


def test_import_reproduces_supersede_state(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    # a correct_memory produces archive(old)+create(new) events; replaying them by HLC must
    # reconstruct the SAME active set AND preserve the archived old row (notes==fts==vec holds).
    src = _mem(str(tmp_path / "src.db"), frozen_clock)
    aid = src.add("alpha fact").added[0].id
    src.add("beta fact")
    src.correct_memory(aid, "alpha corrected")
    lines = list(src.export_events())

    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    dst.import_events(lines)
    assert sorted(n.content for n in dst.list_active()) == ["alpha corrected", "beta fact"]
    h = dst.health()
    assert h["notes"] == h["fts"] == h["vec"] == 3  # 2 active + 1 archived old-alpha, no drift


# ── import hardening (security dogfood): the import path bypasses WriteCore, so it must run the
# same admission + isolation + terminality guarantees the normal add/purge paths give ─────────────
def _fresh_note_line(note_id: str, *, content: str, hlc: str, op: str = "create") -> str:
    """An NDJSON event line carrying a from-scratch note snapshot (no local row required)."""
    note = Note(
        id=note_id,
        content=content,
        memory_type="semantic",
        scope=Scope(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        sources=[  # a valid provenance row (I14) so the note would materialize absent our guards
            Source(
                kind="manual",
                ref="import-test",
                content_hash="deadbeef",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )
    ev = Event(
        event_id=f"ext-{hlc}-{note_id[:6]}",
        device_id="other-device",
        hlc=hlc,
        entity="note",
        entity_id=note_id,
        op=op,
        payload=note.model_dump_json(),
        ts=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return ev.model_dump_json()


def test_import_drops_secret_bearing_note(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    # I6: import is the one write path that skips WriteCore's pre-disk secret BLOCK. A note payload
    # carrying an obvious secret must never be materialized OR recorded in the event log.
    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    line = _fresh_note_line(
        "n" * 32, content="deploy creds AKIAIOSFODNN7EXAMPLE rotate them", hlc="9999999999999:0:x"
    )
    res = dst.import_events([line])
    assert res.materialized == 0
    assert not dst.list_active()  # not stored
    assert not dst.search("AKIAIOSFODNN7EXAMPLE").hits  # not FTS-indexed
    # never reaches disk — not even the append-only event log (I6/I2)
    assert all("AKIAIOSFODNN7EXAMPLE" not in line for line in dst.export_events())


def test_import_isolates_one_malformed_payload(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    # one bad payload must not abort the whole import (all-or-nothing DoS) — good facts still land.
    src = _mem(str(tmp_path / "src.db"), frozen_clock)
    src.add("good fact alpha")
    src.add("good fact beta")
    lines = list(src.export_events())
    bad = json.loads(_fresh_note_line("b" * 32, content="x", hlc="5000000000000:0:x"))
    bad["payload"] = "{not valid json"
    lines.insert(1, json.dumps(bad))

    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    res = dst.import_events(lines)
    assert {n.content for n in dst.list_active()} == {"good fact alpha", "good fact beta"}
    assert res.skipped >= 1  # the malformed event counted, not raised


def test_purge_is_terminal_under_import(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    # a hard-purge is not revivable (I2/D16); a crafted higher-HLC import must NOT resurrect it.
    m = _mem(str(tmp_path / "m.db"), frozen_clock)
    nid = m.add("a fact that will be purged").added[0].id
    m.purge(nid)
    revive = _fresh_note_line(nid, content="resurrected content", hlc="9999999999999:0:x")
    m.import_events([revive])
    assert all(n.id != nid for n in m.list_active())  # stays purged


def test_import_drops_oversized_note(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    # bounded-growth intent: a pathologically large note payload is rejected, not materialized.
    dst = _mem(str(tmp_path / "dst.db"), frozen_clock)
    line = _fresh_note_line("z" * 32, content="x" * (2 * 1024 * 1024), hlc="9999999999999:0:x")
    res = dst.import_events([line])
    assert res.materialized == 0
    assert not dst.list_active()


def test_cli_import_events(tmp_path: Path) -> None:
    src = str(tmp_path / "src.db")
    Memory(src).add("cli event-log fact")
    dump = str(tmp_path / "events.ndjson")
    assert main(["--db", src, "export", dump, "--events"]) == 0
    dst = str(tmp_path / "dst.db")
    assert main(["--db", dst, "import", dump, "--events"]) == 0
    assert any("cli event-log fact" in n.content for n in Memory(dst).list_active())
