"""Perf smoke (D.1) — a `slow`, nightly-only latency floor, NOT in the merge gate.

The D.1 budgets were 'aspirational, un-gated' with the perf test 'NOT BUILT'. This builds it: at the
realistic worst case (a full 2000-fact semantic scope — the per-scope cap), add/search must stay
fast. Budgets are DELIBERATELY generous absolute ceilings (~10-20x the local baseline of add p95
~6 ms / search p95 ~10 ms) so they never flake on a shared CI runner — they catch an
order-of-magnitude regression (an accidental O(n^2) write, a per-query full-table scan, a lost KNN
vectorization), not micro-noise. Runs in the nightly workflow on a pinned runner class.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder

pytestmark = pytest.mark.slow

_N_FACTS = (
    2000  # the per-scope semantic cap — the largest active set a single scope ever ranks over
)
_N_QUERIES = 200
_ADD_P95_MS = 100.0  # local ~6 ms; generous so only an order-of-magnitude regression trips it
_SEARCH_P95_MS = 250.0  # local ~10 ms; also under the D.1 100k `search` ceiling of 400 ms


def _p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, round(0.95 * len(samples)) - 1)]


def test_add_and_search_latency_stays_bounded() -> None:
    db = str(Path(tempfile.mkdtemp()) / "perf.db")
    # consolidate_every huge so a background roll-up never perturbs the timing
    mem = Memory(db, embedder=HashEmbedder(), consolidate_every=10**9)

    add_ms: list[float] = []
    for i in range(_N_FACTS):
        fact = f"fact number {i} about topic {i % 50} with value {i * 7}"
        t = time.perf_counter()
        mem.add(fact, raw=True)  # raw → no LLM extract; pure write-path timing
        add_ms.append((time.perf_counter() - t) * 1000)

    search_ms: list[float] = []
    for i in range(_N_QUERIES):
        t = time.perf_counter()
        mem.search(f"topic {i % 50} value", k=10)
        search_ms.append((time.perf_counter() - t) * 1000)

    add_p95, search_p95 = _p95(add_ms), _p95(search_ms)
    assert add_p95 < _ADD_P95_MS, (
        f"add p95 {add_p95:.1f}ms over {_ADD_P95_MS}ms at {_N_FACTS} facts"
    )
    assert search_p95 < _SEARCH_P95_MS, (
        f"search p95 {search_p95:.1f}ms over {_SEARCH_P95_MS}ms at {_N_FACTS} facts"
    )
