"""Recall-quality benchmark tests (the adoption proof, audit #4).

Proves the honest, reproducible finding: the offline HashEmbedder gets ~full recall on lexical
queries but drops sharply on paraphrases (the vocabulary gap a bag-of-words embedder can't bridge),
and the semantic [local-llm] embedder lifts paraphrase recall. The hash gap runs in CI; the local
lift runs only where sentence-transformers is installed.
"""

from __future__ import annotations

import importlib.util

import pytest
from cold_frame.eval.recall_bench import evaluate
from cold_frame.llm.base import HashEmbedder

_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None


def test_hash_recall_exposes_the_lexical_paraphrase_gap() -> None:
    r = evaluate(HashEmbedder())
    # lexical queries share words → BM25/hash find them almost always.
    assert r["lexical"] >= 0.9
    # paraphrases (near-zero token overlap) → the bag-of-words default can't bridge the gap.
    assert r["paraphrase"] <= 0.5
    # the GAP is the whole point (this is why semantic recall matters).
    assert r["lexical"] - r["paraphrase"] >= 0.4


@pytest.mark.skipif(not _HAS_ST, reason="needs the [local-llm] extra (sentence-transformers)")
def test_local_embedder_lifts_paraphrase_recall() -> None:
    from cold_frame.llm.local import SentenceTransformerEmbedder

    hash_r = evaluate(HashEmbedder())
    local_r = evaluate(SentenceTransformerEmbedder())
    # semantic embeddings bridge the vocabulary gap → strictly better paraphrase recall.
    assert local_r["paraphrase"] > hash_r["paraphrase"]
