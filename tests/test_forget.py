"""Forgetting tests (P4 unit 1): capacity cap archive + pin/forget/revive + convergence.

Archive-not-delete (I2): capped/forgotten notes stay as rows (status=archived), revivable.
Pinned/high never archived; consolidate is convergent (re-run = no-op) — I13.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.models import Note, Scope, Source
from cold_frame.prompts.consolidate import ConsolidationOutput

from tests.conftest import FrozenClock, ScriptedLLM


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


# ── P4-2: LLM episodic → semantic consolidation ───────────────────────────────
def _consolidating_memory(db_path: str, clock: FrozenClock) -> tuple[Memory, ScriptedLLM]:
    llm = ScriptedLLM(
        {
            TaskTag.CONSOLIDATE_SUMMARY: LLMResult(
                parsed=ConsolidationOutput(
                    summary="User likes dark roast coffee", keywords=["coffee"]
                )
            )
        }
    )
    return Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=clock), llm


def test_consolidate_merges_episodic_cluster(db_path: str, frozen_clock: FrozenClock) -> None:
    m, llm = _consolidating_memory(db_path, frozen_clock)
    _seed(m, "a", "dark roast coffee")  # seeded directly → bypass add-time dedup/conflict judges
    _seed(m, "b", "dark roast coffee beans")  # cosine 0.866 → same cluster
    res = m.consolidate(caps={"episodic": 1000, "semantic": 1000})  # high caps → isolate the merge

    assert TaskTag.CONSOLIDATE_SUMMARY in llm.calls
    assert len(res.merged) == 1
    summary = m.get(res.merged[0])
    assert summary.memory_type == "semantic"  # episodic cluster distilled to a standing fact
    assert "dark roast coffee" in summary.content
    # derived_from edges link the summary back to BOTH sources (non-destructive)
    assert {e.dst_id for e in m.neighbors(res.merged[0], relations=["derived_from"])} == {"a", "b"}
    assert m.get("a").decay_S < 1.0  # sources cold-demoted (fade faster), not deleted


def test_consolidate_is_convergent_no_remerge(db_path: str, frozen_clock: FrozenClock) -> None:
    m, _ = _consolidating_memory(db_path, frozen_clock)
    _seed(m, "a", "dark roast coffee")
    _seed(m, "b", "dark roast coffee beans")
    r1 = m.consolidate(caps={"episodic": 1000, "semantic": 1000})
    r2 = m.consolidate(caps={"episodic": 1000, "semantic": 1000})
    assert len(r1.merged) == 1
    assert r2.merged == []  # sources already consumed (incoming derived_from) → no re-merge


# ── auto-maintenance: debounced consolidate every N writes (I13, G1) ──────────
def test_auto_consolidate_triggers_every_n_writes(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock, consolidate_every=3)
    for i in range(3):
        m.add(f"distinct fact number {i} about a topic", raw=True)
    # the 3rd new-fact write crossed the threshold → a consolidate job ran to completion
    done = m._store._conn.execute(
        "SELECT count(*) FROM jobs WHERE kind='consolidate' AND status='done'"
    ).fetchone()[0]
    assert done >= 1


def test_no_auto_consolidate_before_threshold(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock, consolidate_every=5)
    m.add("a single fact", raw=True)
    jobs = m._store._conn.execute("SELECT count(*) FROM jobs WHERE kind='consolidate'").fetchone()[
        0
    ]
    assert jobs == 0  # below threshold → no maintenance scheduled


def test_auto_consolidate_retriggers_each_window(db_path: str, frozen_clock: FrozenClock) -> None:
    # crossing the threshold twice (8 writes / window 4) runs maintenance twice, fully automatic
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock, consolidate_every=4)
    for i in range(8):
        m.add(f"unrelated fact {i} xyz{i}", raw=True)
    done = m._store._conn.execute(
        "SELECT count(*) FROM jobs WHERE kind='consolidate' AND status='done'"
    ).fetchone()[0]
    assert done >= 2  # two windows → two consolidate cycles, no manual call


def test_strength_imminent_subflag_for_archive_imminent_notes() -> None:
    # FADING_EMBER sub-label: a fading note below the threshold is flagged archive-imminent; a
    # healthy note is not. (Strength is computed, not stored — no round-trip concern.)
    from cold_frame.read.strength import compute_strength

    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    now = datetime(2026, 6, 1, tzinfo=UTC)

    def _n(**fields: object) -> Note:
        return Note(
            id="x",
            content="c",
            memory_type="semantic",
            scope=Scope(),
            created_at=t0,
            sources=[Source(kind="message", ref="m", content_hash="h", observed_at=t0)],
            **fields,  # type: ignore[arg-type]
        )

    weak = _n(decay_S=0.2, importance=0.0, last_accessed=t0)  # old + low-value + fast decay
    s = compute_strength(weak, now)
    assert s.band == "fading" and s.imminent is True
    healthy = _n(importance=0.9, last_accessed=now)
    assert compute_strength(healthy, now).imminent is False
    # fading but ABOVE the ember threshold → fading, NOT imminent (pins `value < FADING_EMBER`):
    # value ≈ 0.35·0.4 = 0.14, which is in [FADING_EMBER=0.10, BAND_BUDDING=0.33)
    ember = compute_strength(_n(decay_S=0.2, importance=0.4, last_accessed=t0), now)
    assert ember.band == "fading" and ember.imminent is False
    # a pinned note is NEVER archive-imminent even when weak (I13 exempts pinned from archive)
    pinned = _n(decay_S=0.2, importance=0.0, last_accessed=t0, pinned=True)
    assert compute_strength(pinned, now).imminent is False
