"""RRF fusion tests (P1 unit 6): k_const=60, no global divisor, deterministic ties."""

from __future__ import annotations

import pytest
from cold_frame.constants import RRF_K
from cold_frame.read.fuse import rrf_fuse


def test_rrf_no_global_divisor() -> None:
    result = rrf_fuse({"semantic": ["a", "b"], "bm25": ["b", "c"]}, RRF_K)
    scores = dict(result)
    # b: rank1 semantic + rank0 bm25; a: rank0 semantic; c: rank1 bm25
    assert scores["b"] == pytest.approx(1 / (1 + 60) + 1 / (0 + 60))
    assert scores["a"] == pytest.approx(1 / (0 + 60))
    assert scores["c"] == pytest.approx(1 / (1 + 60))
    assert [nid for nid, _ in result] == ["b", "a", "c"]


def test_rrf_tie_break_by_recency() -> None:
    # x and y are each rank-0 in a single channel → equal RRF; recency breaks the tie.
    rank = {"x": 0, "y": 1}  # x is more recent (lower recency rank)
    result = rrf_fuse({"semantic": ["x"], "bm25": ["y"]}, RRF_K, recency_rank=lambda nid: rank[nid])
    assert [nid for nid, _ in result] == ["x", "y"]


def test_rrf_edge_channel_is_weight_scaled() -> None:
    full = dict(rrf_fuse({"edge": ["h"]}, RRF_K, weight_fn=lambda _nid: 1.0))
    half = dict(rrf_fuse({"edge": ["h"]}, RRF_K, weight_fn=lambda _nid: 0.5))
    assert half["h"] == pytest.approx(full["h"] * 0.5)  # promiscuity down-weight
