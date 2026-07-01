"""Opt-in LLM rerank (read-and-budget §5.7): re-score top candidates by query relevance.

Off by default (the deterministic meta-boost path stands). When ``rerank=True`` AND an LLM is
configured, an RERANK_JUDGE call scores the top candidates and the result is re-sorted (Signals).
A failure/no-LLM must never DROP results — the fused order stands.
"""

from __future__ import annotations

from cold_frame.api import Memory
from cold_frame.llm.base import LLM, HashEmbedder, LLMResult, TaskTag
from cold_frame.prompts.rerank import RerankOutput, RerankScore
from pydantic import BaseModel

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


def test_rerank_empty_scores_keeps_fused_order(db_path: str, frozen_clock: FrozenClock) -> None:
    # a VALID but empty RerankOutput takes the scored-mapping branch (not the isinstance early-out):
    # every hit gets rerank=None, so the stable sort preserves fused order and nothing is dropped.
    llm = ScriptedLLM(
        {TaskTag.RERANK_JUDGE: LLMResult(parsed=RerankOutput(scores=[]))}, is_local=True
    )
    m = _mem(db_path, frozen_clock, llm)
    m.add(_A, raw=True)
    m.add(_B, raw=True)
    base = [h.note.id for h in m.search(_QUERY, k=10).hits]
    res = m.search(_QUERY, k=10, rerank=True)
    assert [h.note.id for h in res.hits] == base
    assert all(h.signals.rerank is None for h in res.hits)


def test_rerank_llm_transport_error_keeps_fused_order(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # a genuine provider/transport failure (complete() RAISES) must degrade to fused order, never
    # crash search() — the fail-soft promise. (A ScriptedLLM undeclared-call AssertionError still
    # surfaces; this covers the OTHER branch: a real backend exception.)
    class _RaisingLLM(LLM):
        name = "boom"

        @property
        def is_local(self) -> bool:
            return True

        def complete(
            self,
            *,
            task: TaskTag,
            system: str,
            user: str,
            schema: type[BaseModel] | None = None,
            temperature: float = 0.0,
            max_tokens: int = 1024,
        ) -> LLMResult:
            raise RuntimeError("provider transport failure")

    m = Memory(db_path, embedder=HashEmbedder(), llm=_RaisingLLM(), clock=frozen_clock)
    m.add(_A, raw=True)
    m.add(_B, raw=True)
    base = [h.note.id for h in m.search(_QUERY, k=10).hits]
    res = m.search(_QUERY, k=10, rerank=True)  # must NOT propagate the RuntimeError
    assert [h.note.id for h in res.hits] == base  # fused order preserved, results not dropped
