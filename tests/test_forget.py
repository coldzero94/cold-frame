"""Forgetting tests (P4 unit 1): capacity cap archive + pin/forget/revive + convergence.

Archive-not-delete (I2): capped/forgotten notes stay as rows (status=archived), revivable.
Pinned/high never archived; consolidate is convergent (re-run = no-op) — I13.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Note, Scope, Source


def _add_many(memory: Memory, contents: list[str]) -> list[str]:
    return [memory.add(c).added[0].id for c in contents]


def _seed(memory: Memory, nid: str, content: str, **fields: object) -> None:
    created = fields.pop("created_at", datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC))
    note = Note(
        id=nid,
        content=content,
        memory_type="episodic",
        scope=Scope(),
        created_at=created,  # type: ignore[arg-type]
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=created)],  # type: ignore[arg-type]
        **fields,  # type: ignore[arg-type]
    )
    memory._store.add_note(note, HashEmbedder().embed_one(content))


def test_capacity_cap_archives_down_to_cap(memory: Memory) -> None:
    _add_many(memory, ["fact one cats", "fact two dogs", "fact three birds", "fact four fish"])
    res = memory.consolidate(caps={"episodic": 2})
    assert len(memory.list_active()) == 2  # capped
    assert len(res.archived) == 2
    # archive-not-delete: the archived rows are still present and revivable
    archived = memory.get(res.archived[0])
    assert archived.status == "archived"


def test_pinned_never_archived(memory: Memory) -> None:
    ids = _add_many(memory, ["alpha fact", "beta fact", "gamma fact"])
    pinned = memory.pin(ids[0])
    assert pinned.pinned is True
    memory.consolidate(caps={"episodic": 1})  # cap 1, but the pinned note is exempt
    assert memory.get(ids[0]).status == "active"  # pinned survives the cap


def test_forget_and_revive_roundtrip(memory: Memory) -> None:
    fid = memory.add("I prefer dark roast coffee").added[0].id
    assert memory.forget(fid).status == "archived"
    assert memory.search("coffee").hits == []  # archived excluded from default search
    assert memory.revive(fid).status == "active"
    assert memory.search("coffee").hits[0].note.id == fid


def test_consolidate_is_convergent(memory: Memory) -> None:
    _add_many(memory, ["a fact one", "b fact two", "c fact three"])
    memory.consolidate(caps={"episodic": 1})
    second = memory.consolidate(caps={"episodic": 1})
    assert second.archived == []  # already at cap → re-run is a no-op
    assert len(memory.list_active()) == 1


def test_no_unbounded_growth(memory: Memory) -> None:
    for i in range(20):
        memory.add(f"fact number {i} about a distinct topic")
    memory.consolidate(caps={"episodic": 5})
    assert len(memory.list_active()) <= 5  # R5: forgetting bounds the active set


def test_high_importance_note_survives_cap(memory: Memory) -> None:
    _seed(memory, "low", "a low value note", importance=0.2)
    _seed(memory, "high", "a high value note", importance=0.9)
    memory.consolidate(caps={"episodic": 0})  # cap 0 → archive every non-protected note
    assert memory.get("high").status == "active"  # high-importance exempt (I13)
    assert memory.get("low").status == "archived"


def test_decay_archive_path_archives_weak_low_value(memory: Memory) -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    _seed(
        memory, "stale", "an old low value note", importance=0.1, last_accessed=old, created_at=old
    )
    later = datetime(2026, 12, 1, tzinfo=UTC)  # ~11 months: retrievability ~0 → S<0.33
    res = memory.consolidate(
        now=later, caps={"episodic": 1000}
    )  # cap huge → only decay can archive
    assert "stale" in res.archived
    assert memory.get("stale").status == "archived"


def test_forget_co_writes_archive_event(memory: Memory) -> None:
    fid = memory.add("dark roast coffee").added[0].id
    memory.forget(fid)
    n = memory._store._conn.execute(
        "SELECT count(*) FROM events WHERE op='archive' AND entity_id=?", (fid,)
    ).fetchone()[0]
    assert n == 1  # I3/I17: the archive grain is co-written to the event log


def test_revive_clears_invalid_at_on_superseded_note(memory: Memory) -> None:
    a = memory.add("I work at Vessl").added[0].id
    now = memory._clock.now()
    new = Note(
        id="newjob",
        content="I work at Anthropic",
        memory_type="episodic",
        scope=Scope(),
        created_at=now,
        valid_at=now,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=now)],
    )
    memory._store.supersede(a, new, HashEmbedder().embed_one(new.content))
    assert memory.get(a).invalid_at is not None  # superseded → invalidated
    revived = memory.revive(a)
    assert revived.status == "active" and revived.invalid_at is None  # revive clears it (I2)
