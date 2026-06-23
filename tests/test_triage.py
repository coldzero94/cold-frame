"""A4: human-in-the-loop Triage queue (triage_queue / resolve_triage).

A note flagged ``held_for_human`` (low-confidence / true-conflict / ambiguous-merge)
is excluded from default search and surfaced ONLY through the Triage queue, where a
human resolves it. The five resolve actions (pin/keep/let_go/merge/supersede) all
clear the hold; merge/supersede require an opposing ``target`` id.
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import NoteNotFound
from cold_frame.llm.base import HashEmbedder

from tests.conftest import FrozenClock


def _mem(db_path: str, clock: FrozenClock) -> Memory:
    return Memory(db_path, embedder=HashEmbedder(), llm=None, clock=clock)  # offline


def _held_fact(m: Memory, text: str, *, reason: str = "low_confidence") -> str:
    """Create a fact and flag it held_for_human (as consolidation/admission would)."""
    fid = m.create_fact(text).added[0].id
    m._store.set_held_for_human(fid, held=True, quarantined=True, reason=reason)
    return fid


def test_held_note_surfaces_in_triage_queue(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "maybe I prefer oat milk", reason="ambiguous_merge")
    queue = m.triage_queue()
    assert [item.note.id for item in queue] == [fid]
    assert queue[0].reason == "ambiguous_merge"
    assert queue[0].impact == m.get(fid).importance


def test_active_unheld_note_not_in_queue(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    m.create_fact("I prefer dark roast")  # confident, not held
    assert m.triage_queue() == []


def test_held_note_excluded_from_default_search(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "I tried a new espresso blend")
    assert m.search("espresso").hits == []  # quarantined → out of default search (I14)
    assert [item.note.id for item in m.triage_queue()] == [fid]  # visible only via Triage


def test_resolve_pin_accepts_and_clears(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "I lead the platform team")
    m.resolve_triage(fid, "pin")
    assert m.triage_queue() == []  # cleared from queue
    note = m.get(fid)
    assert note.status == "active" and note.pinned and not note.held_for_human


def test_resolve_keep_accepts_and_clears(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "I use vim keybindings")
    m.resolve_triage(fid, "keep")
    assert m.triage_queue() == []
    note = m.get(fid)
    assert note.status == "active" and not note.pinned and not note.held_for_human


def test_resolve_let_go_archives(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "a passing thought")
    m.resolve_triage(fid, "let_go")
    assert m.triage_queue() == []
    archived = m.get(fid)
    assert archived.status == "archived"  # revivable (I2), not deleted
    # the hold is CLEARED (not just masked by status): a later revive must not resurface it
    assert archived.held_for_human is False and archived.quarantined is False


def test_resolve_let_go_does_not_resurface_on_revive(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "a resolved-then-revived thought")
    m.resolve_triage(fid, "let_go")
    m.revive(fid)  # status→active again; the human's let_go decision must still hold
    assert m.triage_queue() == []  # NOT back in the queue (hold was cleared, not just masked)


def test_resolve_supersede_bad_target_keeps_held(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    held = _held_fact(m, "I work at Anthropic", reason="true_conflict")
    with pytest.raises(NoteNotFound):
        m.resolve_triage(held, "supersede", target="ghost")  # forget(target) first → raises
    # no partial resolve: the held note is untouched, still held, still queued
    assert [item.note.id for item in m.triage_queue()] == [held]
    assert m.get(held).held_for_human is True


def test_resolve_merge_archives_held_note(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    keep = m.create_fact("I prefer dark roast coffee").added[0].id
    dup = _held_fact(m, "I like dark roast", reason="ambiguous_merge")
    m.resolve_triage(dup, "merge", target=keep)
    assert m.triage_queue() == []
    assert m.get(dup).status == "archived"  # the held duplicate folds away
    assert m.get(keep).status == "active"  # the canonical note survives


def test_resolve_supersede_keeps_held_archives_target(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m = _mem(db_path, frozen_clock)
    old = m.create_fact("I work at Vessl").added[0].id
    new = _held_fact(m, "I work at Anthropic", reason="true_conflict")
    m.resolve_triage(new, "supersede", target=old)
    assert m.triage_queue() == []
    assert m.get(new).status == "active" and not m.get(new).held_for_human
    assert m.get(old).status == "archived"  # the held note won


@pytest.mark.parametrize("action", ["merge", "supersede"])
def test_merge_supersede_require_target(
    db_path: str, frozen_clock: FrozenClock, action: str
) -> None:
    m = _mem(db_path, frozen_clock)
    fid = _held_fact(m, "ambiguous fact")
    with pytest.raises(ValueError, match="requires a target"):
        m.resolve_triage(fid, action)  # type: ignore[arg-type]
    assert m.triage_queue()  # untouched — still in the queue


def test_triage_queue_ranks_by_importance(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    low = _held_fact(m, "minor detail")
    high = _held_fact(m, "critical detail")
    m.update(high, importance=0.95)
    m.update(low, importance=0.1)
    assert [item.note.id for item in m.triage_queue()] == [high, low]
