"""Opt-in LLM rerank (read-and-budget §5.7): re-score top candidates by query relevance.

Off by default (the deterministic meta-boost path stands). When ``rerank=True`` AND an LLM is
configured, an RERANK_JUDGE call scores the top candidates and the result is re-sorted (Signals).
A failure/no-LLM must never DROP results — the fused order stands.
"""

from __future__ import annotations

from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.prompts.rerank import RerankOutput, RerankScore

from tests.conftest import FrozenClock, ScriptedLLM

# two facts with NO shared tokens (cosine ~0 → no dedup-judge LLM call on the 2nd add); the query
# matches each via its own distinctive token.
_A = "zzappa coffee roaster downtown"
_B = "qqberry espresso weekly purchase"
_QUERY = "coffee espresso"


def _mem(db_path: str, clock: FrozenClock, llm: ScriptedLLM) -> Memory:
    return Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=clock)


def test_rerank_reorders_by_llm_relevance(db_path: str, frozen_clock: FrozenClock) -> None:
    llm = ScriptedLLM({}, is_local=True)
    m = _mem(db_path, frozen_clock, llm)
    a = m.add(_A, raw=True).added[0].id
    b = m.add(_B, raw=True).added[0].id
    assert {a, b} <= {h.note.id for h in m.search(_QUERY, k=10).hits}  # both retrievable
    # script the reranker to put b strictly above a
    llm._script[TaskTag.RERANK_JUDGE] = LLMResult(
        parsed=RerankOutput(
            scores=[RerankScore(id=b, relevance=0.99), RerankScore(id=a, relevance=0.05)]
        )
    )
    res = m.search(_QUERY, k=10, rerank=True)
    assert res.hits[0].note.id == b  # rerank promoted b to the top
    assert next(h for h in res.hits if h.note.id == b).signals.rerank == 0.99  # signal set


def test_rerank_off_by_default_makes_no_llm_call(db_path: str, frozen_clock: FrozenClock) -> None:
    llm = ScriptedLLM({}, is_local=True)
    m = _mem(db_path, frozen_clock, llm)
    m.add(_A, raw=True)
    m.search(_QUERY)  # rerank defaults to False
    assert TaskTag.RERANK_JUDGE not in llm.calls


def test_rerank_with_no_llm_is_noop(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock)
    m.add(_A, raw=True)
    res = m.search(_QUERY, rerank=True)  # no LLM → graceful no-op, still returns results
    assert res.hits


def test_rerank_unparseable_keeps_fused_order(db_path: str, frozen_clock: FrozenClock) -> None:
    llm = ScriptedLLM({TaskTag.RERANK_JUDGE: LLMResult(parsed=None)}, is_local=True)
    m = _mem(db_path, frozen_clock, llm)
    m.add(_A, raw=True)
    m.add(_B, raw=True)
    base = [h.note.id for h in m.search(_QUERY, k=10).hits]
    res = m.search(_QUERY, k=10, rerank=True)  # unparseable verdict → fused order, never dropped
    assert [h.note.id for h in res.hits] == base
