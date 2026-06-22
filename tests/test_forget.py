"""Forgetting tests (P4 unit 1): capacity cap archive + pin/forget/revive + convergence.

Archive-not-delete (I2): capped/forgotten notes stay as rows (status=archived), revivable.
Pinned/high never archived; consolidate is convergent (re-run = no-op) — I13.
"""

from __future__ import annotations

from cold_frame.api import Memory


def _add_many(memory: Memory, contents: list[str]) -> list[str]:
    return [memory.add(c).added[0].id for c in contents]


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
