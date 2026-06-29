"""Tier C: re-embedding migration (I8/I10) + the [local-llm] embedder import-guard.

Swapping the embedder (e.g. installing a local model) leaves existing vectors written under
the old embedder_id; KNN hard-filters embedder_id=current (I10) so they degrade to BM25-only
until ``reembed`` re-indexes them. A second HashEmbedder with a distinct ``name`` stands in for
"a different model" so the migration is exercised deterministically — no torch, no download.
"""

from __future__ import annotations

import importlib.util

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import EmbedderMismatchError
from cold_frame.llm.base import EmbedderMeta, HashEmbedder

from tests.conftest import FrozenClock


def _mem(db_path: str, embedder: HashEmbedder, clock: FrozenClock) -> Memory:
    return Memory(db_path, embedder=embedder, llm=None, clock=clock)


def _distinct(store: object, sql: str) -> set[object]:
    return {tuple(r) for r in store._conn.execute(sql).fetchall()}  # type: ignore[attr-defined]


def test_reembed_migrates_stale_vectors(db_path: str, frozen_clock: FrozenClock) -> None:
    m1 = _mem(db_path, HashEmbedder(), frozen_clock)  # hash / 256
    m1.add("I prefer dark roast coffee")
    m1.add("the deploy script is ship.sh")
    assert m1.health()["stale_vectors"] == 0
    m1.close()

    # reopen under a DIFFERENT embedder (distinct id + dim) — the swap-and-reindex scenario
    m2 = _mem(db_path, HashEmbedder(dim=384, name="local:sim-bge"), frozen_clock)
    assert m2.health()["stale_vectors"] == 2  # both hash/256 vectors are stale vs local:sim-bge
    # KNN excludes the stale vectors → the note is found, but ONLY via BM25 (no semantic signal).
    before = m2.search("coffee").hits
    assert before and before[0].signals.semantic is None

    res = m2.reembed()
    assert res.reembedded == 2 and res.embedder_id == "local:sim-bge"
    health = m2.health()
    assert health["stale_vectors"] == 0 and health["dim"] == 384  # all current under new embedder
    # the migration's whole point: the semantic (KNN) channel now fires again, not just BM25.
    after = m2.search("coffee").hits
    assert after and after[0].signals.semantic is not None
    # and the persisted rows were actually rewritten — note_vec AND notes.embedder_id (not masked
    # by doctor reading the live embedder): every vector is now local:sim-bge / 384.
    assert _distinct(m2._store, "SELECT DISTINCT embedder_id, dim FROM note_vec") == {
        ("local:sim-bge", 384)
    }
    assert _distinct(m2._store, "SELECT DISTINCT embedder_id FROM notes") == {("local:sim-bge",)}
    assert m2.reembed().reembedded == 0  # idempotent — nothing stale on a second run
    m2.close()


def test_reembed_touches_only_stale_in_mixed_state(db_path: str, frozen_clock: FrozenClock) -> None:
    m1 = _mem(db_path, HashEmbedder(), frozen_clock)
    m1.add("note embedded under hash")
    m1.close()
    # swap embedder, then add a NEW note — it is written under the new embedder already
    new = HashEmbedder(dim=384, name="local:sim")
    m2 = _mem(db_path, new, frozen_clock)
    m2.add("note written under the new embedder")
    assert m2.health()["stale_vectors"] == 1  # only the hash note is stale; the new one is current
    res = m2.reembed()
    assert res.reembedded == 1  # reembed touches ONLY the stale note, not the already-current one
    assert m2.health()["stale_vectors"] == 0
    m2.close()


def test_reembed_persists_meta_across_reopen(db_path: str, frozen_clock: FrozenClock) -> None:
    _mem(db_path, HashEmbedder(), frozen_clock).close()  # seeds meta hash/256 (no notes)
    m2 = _mem(db_path, HashEmbedder(dim=384, name="local:sim"), frozen_clock)
    m2.reembed()  # no stale vectors, but must fast-forward stored meta to local:sim/384
    m2.close()
    # a THIRD fresh open under the same embedder sees it as current — proving meta persisted
    # (not merely shadowed by the live embedder in a single session).
    m3 = _mem(db_path, HashEmbedder(dim=384, name="local:sim"), frozen_clock)
    assert m3._store.embedder_meta() == EmbedderMeta(embedder_id="local:sim", dim=384)
    assert m3.health()["stale_vectors"] == 0
    m3.close()


def test_reembed_noop_when_embedder_unchanged(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, HashEmbedder(), frozen_clock)
    m.add("a fact about pizza")
    assert m.reembed().reembedded == 0  # same embedder → nothing stale
    m.close()


def test_embedder_swap_different_id_opens_without_raising(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    _mem(db_path, HashEmbedder(), frozen_clock).close()  # seeds meta hash/256
    # a DIFFERENT embedder id (even at a different dim) is a legitimate swap → no raise
    m = _mem(db_path, HashEmbedder(dim=384, name="local:sim"), frozen_clock)
    assert m.health()["embedder_id"] == "local:sim"
    m.close()


def test_same_id_different_dim_still_raises(db_path: str) -> None:
    Memory(db_path, embedder=HashEmbedder(), llm=None).close()  # hash/256
    with pytest.raises(EmbedderMismatchError):  # same id "hash", dim 128 != 256 → incompatible
        Memory(db_path, embedder=HashEmbedder(dim=128), llm=None)


def test_local_embedder_requires_extra() -> None:
    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("sentence-transformers installed → import-guard path not exercised")
    from cold_frame.llm.local import SentenceTransformerEmbedder

    with pytest.raises(ImportError, match="local-llm"):  # helpful 'install the extra' message
        SentenceTransformerEmbedder()


def test_reembed_rolls_back_on_mid_txn_failure(
    db_path: str, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # reembed co-commits vec-retag + notes.embedder_id + the meta flip in ONE txn (I3/I10). A
    # mid-loop failure must roll back ALL of it — else meta lags the retag and KNN's
    # embedder_id=current hard-filter silently drops rows (invisible recall loss). The only
    # multi-grain Store write that lacked a partial-failure rollback test.
    from cold_frame.exceptions import StoreError

    m1 = _mem(db_path, HashEmbedder(), frozen_clock)
    m1.add("I prefer dark roast coffee")
    m1.add("the deploy script is ship.sh")
    m1.close()

    m2 = _mem(db_path, HashEmbedder(dim=384, name="local:sim-bge"), frozen_clock)
    assert m2.health()["stale_vectors"] == 2  # pre-state: both stale under the old embedder
    before_meta = m2._store.embedder_meta()

    def _boom(note_id: str, emb: object, embedder_id: str | None = None) -> None:
        raise RuntimeError("simulated mid-reembed failure")

    monkeypatch.setattr(m2._store, "_insert_vec", _boom)
    with pytest.raises(StoreError):
        m2.reembed()

    # full ROLLBACK: stored meta + stale count unchanged — no partial retag, no SoT↔vector drift
    assert m2._store.embedder_meta() == before_meta
    assert m2.health()["stale_vectors"] == 2
