"""correct_memory tests (P2 unit 5): explicit-id supersede via the single WriteCore.

correct_memory is an explicit-id correction (NOT similarity search) — it routes through
WriteCore.commit_supersede → Store.supersede (I15), archiving the old and linking the new.
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import NoteNotFound
from cold_frame.models import Note
from cold_frame.write.core import WriteCore


def test_correct_memory_supersedes_old(memory: Memory) -> None:
    old_id = memory.add("I prefer light roast coffee").added[0].id
    res = memory.correct_memory(old_id, "I prefer dark roast coffee")

    assert res.archived == old_id
    assert res.new.content == "I prefer dark roast coffee"
    assert memory.get(old_id).status == "archived"  # archive-not-delete
    # the corrected belief is what search now returns
    assert memory.search("coffee").hits[0].note.content == "I prefer dark roast coffee"


def test_correct_memory_unknown_raises(memory: Memory) -> None:
    with pytest.raises(NoteNotFound):
        memory.correct_memory("does-not-exist", "new text here")


def test_correct_routes_through_commit_supersede(
    memory: Memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_id = memory.add("I prefer light roast").added[0].id
    calls: list[str] = []
    orig = WriteCore.commit_supersede

    def spy(self: WriteCore, old: str, new: Note, *, reason: str) -> Note:
        calls.append(old)
        return orig(self, old, new, reason=reason)

    monkeypatch.setattr(WriteCore, "commit_supersede", spy)
    memory.correct_memory(old_id, "I prefer dark roast")
    assert calls == [old_id]  # I15: explicit-id supersede, not a similarity search
