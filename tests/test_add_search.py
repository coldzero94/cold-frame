"""Memory add/search wiring tests (P1 units 5-6): the offline add → recall loop.

Uses the conftest ``memory`` fixture (HashEmbedder + llm=None + FrozenClock) — the
offline default (I5/G6). Unit 5 covers add→get + init/embedder guard + the single
WriteCore persist path (I15); unit 6 adds the search-recall cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound
from cold_frame.llm.base import HashEmbedder
from cold_frame.models import Note, Scope, Source
from cold_frame.write.core import WriteCore


# ── unit 5: add → get, init guard, single persist path ────────────────────────
def test_offline_add_recall(memory: Memory) -> None:
    res = memory.add("I prefer dark roast")
    assert len(res.added) == 1
    assert res.added[0].content == "I prefer dark roast"
    assert res.superseded == [] and res.deduped == [] and res.blocked == [] and res.held == []

    got = memory.get(res.added[0].id)
    assert got.id == res.added[0].id
    assert got.content == "I prefer dark roast"


def test_memory_init_migrates_and_add_works(db_path: str) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None)
    assert len(m.add("hello world").added) == 1  # migrate ran, embedder meta written


def test_memory_embedder_mismatch_raises(db_path: str) -> None:
    Memory(db_path, embedder=HashEmbedder(), llm=None)  # seeds db meta dim=256
    with pytest.raises(EmbedderMismatchError):
        Memory(db_path, embedder=HashEmbedder(dim=128), llm=None)  # dim 128 != stored 256


def test_get_unknown_raises(memory: Memory) -> None:
    with pytest.raises(NoteNotFound):
        memory.get("does-not-exist")


def test_add_routes_through_writecore(memory: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    orig = WriteCore.commit

    def spy(self: WriteCore, candidates: list, **kw: object) -> object:  # type: ignore[type-arg]
        calls.append(len(candidates))
        return orig(self, candidates, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(WriteCore, "commit", spy)
    memory.add("route me through the single persist path")
    assert calls == [1]  # I15: exactly one WriteCore.commit per add


# ── unit 6: offline add → search recall (P1 SPEC acceptance) ──────────────────
def test_offline_add_then_search_recall(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    memory.add("I drive a Ferrari 488 GTB")
    res = memory.search("coffee roast", k=5)
    assert res.hits  # the just-added fact is recalled
    assert "dark roast" in res.hits[0].note.content
    assert all(h.signals.rrf is not None for h in res.hits)  # rrf is the required signal
    assert res.used_tokens is None  # no token budget given
    assert res.truncated is False


def test_search_empty_query_no_match_no_raise(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    res = memory.search("zzzz nonexistent qqqq")
    assert res.hits == []  # no semantic/lexical overlap → empty, never raises


def test_search_scope_isolation(memory: Memory) -> None:
    memory.add("dark roast coffee", scope=Scope(user_id="alice"))
    assert memory.search("coffee", scope=Scope(user_id="bob")).hits == []  # cross_scope guard


def test_search_excludes_quarantined(memory: Memory) -> None:
    res = memory.add("dark roast coffee preference")
    nid = res.added[0].id
    memory._store._conn.execute("UPDATE notes SET quarantined=1 WHERE id=?", (nid,))
    assert memory.search("coffee").hits == []  # default FILTER excludes quarantined (G2)


def test_search_reinforces_returned_hits(memory: Memory) -> None:
    res = memory.add("dark roast coffee")
    nid = res.added[0].id
    memory.search("coffee")
    assert memory.get(nid).access_count == 1  # being surfaced is the reinforcement signal


def test_search_as_of_returns_belief_at_that_time(memory: Memory) -> None:
    """C3 bi-temporal hero: as_of bypasses the status filter + TRUE predicate."""
    t1 = datetime(2026, 1, 1, tzinfo=UTC)  # worked at Vessl from here
    t2 = datetime(2026, 6, 1, tzinfo=UTC)  # switched to Anthropic here
    mid = datetime(2026, 3, 1, tzinfo=UTC)  # belief checkpoint (between)

    old_id = memory.add("I work at Vessl", observed_at=t1).added[0].id
    # drive the conflict commit at the Store level (the WriteCore conflict path is P2-4)
    new = Note(
        id="new-job",
        content="I work at Anthropic",
        memory_type="episodic",
        scope=Scope(),
        created_at=t2,
        valid_at=t2,
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=t2)],
    )
    memory._store.supersede(old_id, new, HashEmbedder().embed_one(new.content))

    now_hits = memory.search("where do I work").hits
    assert now_hits and "Anthropic" in now_hits[0].note.content  # current active belief

    mid_hits = memory.search("where do I work", as_of=mid).hits
    assert mid_hits and "Vessl" in mid_hits[0].note.content  # what was TRUE at `mid`


# ── audit fixes: archived recall + historical-read must not reinforce ──────────
def test_search_include_archived_surfaces_a_forgotten_note(memory: Memory) -> None:
    # include_archived is a documented public param; without as_of it was silently a no-op because
    # the "currently in effect" gate filtered archived rows back out. It must surface them.
    nid = memory.add("I deploy with ship.sh").added[0].id
    memory.forget(nid)  # archive (not delete)
    assert memory.search("deploy").hits == []  # default excludes archived
    hits = memory.search("deploy", include_archived=True).hits
    assert any(h.note.id == nid for h in hits)  # now revivable via search


def test_as_of_search_does_not_reinforce_archived_belief(memory: Memory) -> None:
    # a historical (rewind) read must not bump decay/access of the surfaced note.
    nid = memory.add("I deploy with ship.sh").added[0].id
    memory.search("deploy")  # a normal read reinforces
    bumped = memory.get(nid).access_count
    assert bumped >= 1
    memory.search("deploy", as_of=datetime(2099, 1, 1, tzinfo=UTC))  # historical read
    assert memory.get(nid).access_count == bumped  # unchanged — no reinforce on as_of


def test_edge_channel_surfaces_neighbor_and_isolates_scope(memory: Memory) -> None:
    # the edge channel expands a query hit to its 1-hop graph neighbors — a connected fact surfaces
    # even with NO lexical/semantic match — but never across a scope boundary (leak guard).
    from cold_frame.models import Edge, Scope

    s = Scope(user_id="u")
    a = memory.add("the deploy script is ship.sh", scope=s).added[0].id
    b = memory.add("xylophone zucchini quasar widget", scope=s).added[0].id  # unrelated text
    other = memory.add("the deploy script is ship.sh", scope=Scope(user_id="other")).added[0].id
    now = datetime(2030, 1, 1, tzinfo=UTC)
    memory._store.add_edge(Edge(src_id=a, dst_id=b, relation="relates_to", created_at=now))
    memory._store.add_edge(Edge(src_id=a, dst_id=other, relation="relates_to", created_at=now))

    hits = memory.search("deploy script", scope=s).hits
    ids = [h.note.id for h in hits]
    assert a in ids and b in ids  # B surfaced purely via its edge to A
    assert other not in ids  # a cross-scope edge-reached note is filtered out (no leak)
    b_hit = next(h for h in hits if h.note.id == b)
    assert b_hit.signals.edge is not None  # carries the edge signal (reached via the graph)


def test_edge_channel_excludes_quarantined_and_cross_agent(memory: Memory) -> None:
    # the edge channel must mirror the Store search guard: an edge-reached note that is quarantined
    # (I14) or in a different agent/session (same user) must NOT leak into default results.
    from cold_frame.models import Edge, Scope

    s = Scope(user_id="u", agent_id="A")
    a = memory.add("deploy this repo with ship.sh", scope=s).added[0].id
    q = memory.add("quarantined neighbor zzqqxx", scope=s).added[0].id
    cross = (
        memory.add("other-agent neighbor zzqqxx", scope=Scope(user_id="u", agent_id="B"))
        .added[0]
        .id
    )
    memory._store._conn.execute("UPDATE notes SET quarantined=1 WHERE id=?", (q,))
    now = datetime(2030, 1, 1, tzinfo=UTC)
    memory._store.add_edge(Edge(src_id=a, dst_id=q, relation="relates_to", created_at=now))
    memory._store.add_edge(Edge(src_id=a, dst_id=cross, relation="relates_to", created_at=now))

    ids = [h.note.id for h in memory.search("deploy", scope=s).hits]
    assert a in ids  # the seed
    assert q not in ids  # quarantined edge-neighbor excluded (I14)
    assert cross not in ids  # cross-agent edge-neighbor excluded (scope guard)


def test_edge_channel_promiscuity_downweights_high_degree_hub(memory: Memory) -> None:
    # the channel's core down-weighting: a neighbor reached via a LOW-degree hub outranks one
    # reached via a HIGH-degree (promiscuous) hub — w = 1/(1 + PENALTY·(degree-1)²).
    from cold_frame.models import Edge, Scope

    s = Scope(user_id="u")
    now = datetime(2030, 1, 1, tzinfo=UTC)
    lo = memory.add("alpha deploy pipeline notes", scope=s).added[0].id  # low-degree hub
    hi = memory.add("beta deploy pipeline notes", scope=s).added[0].id  # high-degree hub
    b = memory.add("xylophone quasar widget", scope=s).added[0].id  # lo's only neighbor
    c = memory.add("kazoo nimbus gizmo", scope=s).added[0].id  # one of hi's many neighbors
    memory._store.add_edge(Edge(src_id=lo, dst_id=b, relation="relates_to", created_at=now))
    memory._store.add_edge(Edge(src_id=hi, dst_id=c, relation="relates_to", created_at=now))
    for i in range(20):  # inflate hi's degree so the promiscuity penalty bites
        # token-distinct so HashEmbedder doesn't merge them as near-dups (no shared tokens)
        d = memory.add(f"throwaway{i}xqz uniquetoken{i}wbn record{i}mmc", scope=s).added[0].id
        memory._store.add_edge(Edge(src_id=hi, dst_id=d, relation="relates_to", created_at=now))

    hits = {h.note.id: h for h in memory.search("deploy pipeline", scope=s, k=40).hits}
    assert b in hits and c in hits  # both surface purely via their edges
    assert hits[b].signals.edge is not None and hits[c].signals.edge is not None
    assert hits[b].signals.edge > hits[c].signals.edge  # low-degree neighbor weighted higher


def test_edge_channel_skipped_for_historical_as_of_reads(memory: Memory) -> None:
    # the edge channel is a "currently relevant" expansion → skipped for as_of (point-in-time)
    # reads, so a historical query never surfaces graph neighbors that weren't direct hits.
    from cold_frame.models import Edge, Scope

    s = Scope(user_id="u")
    a = memory.add("deploy with ship.sh", scope=s).added[0].id
    b = memory.add("xylophone quasar widget", scope=s).added[0].id  # reachable ONLY via the edge
    memory._store.add_edge(
        Edge(src_id=a, dst_id=b, relation="relates_to", created_at=datetime(2030, 1, 1, tzinfo=UTC))
    )
    assert b in [h.note.id for h in memory.search("deploy", scope=s).hits]  # edge surfaces B
    future = datetime(2099, 1, 1, tzinfo=UTC)  # all notes valid, but the edge channel is skipped
    assert b not in [h.note.id for h in memory.search("deploy", scope=s, as_of=future).hits]


def test_edge_channel_respects_status_filter(memory: Memory) -> None:
    # an edge-reached note that's been archived must not leak into default search, but appears with
    # include_archived (the channel mirrors the knn/bm25 status filter).
    from cold_frame.models import Edge, Scope

    s = Scope(user_id="u")
    a = memory.add("deploy with ship.sh", scope=s).added[0].id
    b = memory.add("xylophone quasar widget", scope=s).added[0].id  # reachable ONLY via the edge
    memory._store.add_edge(
        Edge(src_id=a, dst_id=b, relation="relates_to", created_at=datetime(2030, 1, 1, tzinfo=UTC))
    )
    memory.forget(b)  # archive B
    assert b not in [
        h.note.id for h in memory.search("deploy", scope=s).hits
    ]  # archived → excluded
    archived = memory.search("deploy", scope=s, include_archived=True).hits
    assert b in [h.note.id for h in archived]  # surfaced once archived is included


def test_edge_channel_excludes_currently_invalid_stale_note(memory: Memory) -> None:
    # a "stale" write is status='active' but invalid_at<=now (hidden from current search, visible
    # only via as_of). The edge channel must apply the SAME bi-temporal gate as knn/bm25 — a
    # currently-invalid neighbor must NOT leak into a default (non-historical) search.
    from cold_frame.models import Edge, Note, Scope, Source

    s = Scope(user_id="u")
    t_past = datetime(2020, 1, 1, tzinfo=UTC)
    a = memory.add("deploy with ship.sh", scope=s).added[0].id
    stale = Note(
        id="stale-b",
        content="xylophone quasar widget",  # reachable ONLY via the edge, never lexically
        memory_type="semantic",
        scope=s,
        created_at=t_past,
        valid_at=t_past,
        invalid_at=t_past,  # already invalidated → not currently in effect
        sources=[Source(kind="message", ref="m", content_hash="h", observed_at=t_past)],
    )
    memory._store.add_note(stale, HashEmbedder().embed_one(stale.content))
    memory._store.add_edge(
        Edge(
            src_id=a,
            dst_id="stale-b",
            relation="relates_to",
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    ids = [h.note.id for h in memory.search("deploy", scope=s).hits]
    assert a in ids
    assert "stale-b" not in ids  # currently-invalid note must not leak via the edge channel


def test_memory_type_label_is_not_a_searchable_bm25_term(memory: Memory) -> None:
    # tags (which carry the memory_type label) must NOT be FTS-indexed — else the literal word
    # "episodic"/"semantic" would match every note of that type, polluting BM25. The word does not
    # appear in either content, so a correct index returns nothing for it.
    memory.add("Paris is the capital of France")
    memory.add("The mitochondria is the powerhouse of the cell")
    assert memory.search("episodic").hits == []  # the type label is not a search term
    assert memory.search("Paris").hits  # sanity: real content is still retrievable
