"""Read path (SPEC §5) — retrieve → RRF fuse → meta boost → token-budget pack.

The retrieval moat: hybrid (BM25 + KNN) fan-out, RRF (``k_const=60``, no global divisor),
deterministic meta boost (recency/scope), token-budget packer, REINFORCE on the returned set.
A cross-encoder/LLM rerank backend is a deferred extra, not wired in v1.
"""

from __future__ import annotations

from cold_frame.read.retrieve import RetrievePipeline

__all__ = ["RetrievePipeline"]
