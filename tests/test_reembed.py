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
from cold_frame.llm.base import HashEmbedder

from tests.conftest import FrozenClock


def _mem(db_path: str, embedder: HashEmbedder, clock: FrozenClock) -> Memory:
    return Memory(db_path, embedder=embedder, llm=None, clock=clock)


def test_reembed_migrates_stale_vectors(db_path: str, frozen_clock: FrozenClock) -> None:
    m1 = _mem(db_path, HashEmbedder(), frozen_clock)  # hash / 256
    m1.add("I prefer dark roast coffee")
    m1.add("the deploy script is ship.sh")
    assert m1.health()["stale_vectors"] == 0
    m1.close()

    # reopen under a DIFFERENT embedder (distinct id + dim) — the swap-and-reindex scenario
    m2 = _mem(db_path, HashEmbedder(dim=384, name="local:sim-bge"), frozen_clock)
    assert m2.health()["stale_vectors"] == 2  # both hash/256 vectors are stale vs local:sim-bge
    assert m2.search("coffee").hits  # still found — KNN excludes stale, BM25 carries it

    res = m2.reembed()
    assert res.reembedded == 2 and res.embedder_id == "local:sim-bge"
    health = m2.health()
    assert health["stale_vectors"] == 0 and health["dim"] == 384  # all current under new embedder
    assert m2.search("coffee").hits  # KNN works again
    assert m2.reembed().reembedded == 0  # idempotent — nothing stale on a second run
    m2.close()


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
