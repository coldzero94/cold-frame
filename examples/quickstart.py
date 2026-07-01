"""Coldframe quickstart — the core loop, fully offline (no key, no network).

Run it:  python examples/quickstart.py

Shows: add facts → recall → correct a belief (deterministic supersession) → rewind to what you
believed before. Uses the zero-config default (HashEmbedder). For SEMANTIC recall and automatic
contradiction detection, see examples/README.md (COLD_FRAME_EMBEDDER / COLD_FRAME_LLM).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cold_frame.api import Memory


def main() -> None:
    db = str(Path(tempfile.mkdtemp()) / "quickstart.db")
    mem = Memory(db)  # offline default: one SQLite file, HashEmbedder, no LLM

    print("1) remember a few facts")
    for fact in ("I prefer dark roast coffee", "I deploy with ship.sh", "my editor is Vim"):
        note = mem.add(fact).added[0]
        print(f"   + {note.content}")

    print("\n2) recall by query (hybrid BM25 + vector + RRF)")
    hits = mem.search("what coffee do I like").hits
    print(f"   search('what coffee do I like') → {[h.note.content for h in hits[:1]]}")

    print("\n3) correct a belief — the old fact is superseded, not overwritten")
    coffee_id = next(n.id for n in mem.list_active() if "coffee" in n.content)
    res = mem.correct_memory(coffee_id, "I switched to tea, no more coffee")
    print(f"   corrected → active now: {res.new.content!r}; archived: {res.archived[:8]}")
    print(f"   active beliefs: {sorted(n.content for n in mem.list_active())}")

    print("\n4) rewind — what did I believe before the correction?")
    before = mem.get(coffee_id)  # the archived old belief is still there, revivable
    print(f"   the old belief is retained (status={before.status!r}): {before.content!r}")

    print(f"\ndone — everything lives in {db} (copy it to back up, delete it to forget).")


if __name__ == "__main__":
    main()
