"""Read path (SPEC §5) — retrieve → RRF fuse → meta boost → token-budget pack.

The retrieval moat: hybrid (BM25 + KNN) fan-out, RRF (``k_const=60``, no global divisor),
deterministic meta boost (recency/scope), token-budget packer, REINFORCE on the returned set.
An opt-in LLM rerank (``search(rerank=True)``, ``read/rerank.llm_rerank``) re-scores the top
candidates by query relevance; off by default so the path stays deterministic for eval.
"""

from __future__ import annotations

from cold_frame.read.retrieve import RetrievePipeline

__all__ = ["RetrievePipeline"]
