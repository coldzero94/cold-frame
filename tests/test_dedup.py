"""Tiered dedup tests (P2 unit 3): exact/auto-merge, distinct, ambiguous-band LLM.

Cosines (HashEmbedder, deterministic): identical=1.0 (auto-merge ≥0.93);
"dark roast coffee" vs "dark roast coffee beans"=0.866 (ambiguous band [0.82,0.93));
unrelated=0.18. The LLM only proposes sameness — code disposes (I1).
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.models import ConflictVerdict

from tests.conftest import FrozenClock, ScriptedLLM


def test_exact_duplicate_is_deduped(memory: Memory) -> None:
    first = memory.add("I prefer dark roast coffee").added[0].id
    res = memory.add("I prefer dark roast coffee")  # cosine 1.0 ≥ 0.93 → auto-merge
    assert res.added == []
    assert res.deduped == [first]


def test_distinct_fact_is_kept(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    res = memory.add("I drive a Ferrari 488 GTB")  # cosine ~0.18 < 0.82
    assert len(res.added) == 1
    assert res.deduped == []


def test_ambiguous_band_offline_keeps_both(memory: Memory) -> None:
    memory.add("dark roast coffee")
    res = memory.add("dark roast coffee beans")  # 0.866 band, no LLM → distinct (conservative)
    assert len(res.added) == 1
    assert res.deduped == []


def _llm_memory(db_path: str, clock: FrozenClock, relation: str) -> tuple[Memory, ScriptedLLM]:
    llm = ScriptedLLM(
        {TaskTag.DEDUP_BATCH: LLMResult(parsed=ConflictVerdict(relation=relation, confidence=0.9))}
    )
    return Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=clock), llm


def test_ambiguous_band_llm_duplicate_merges(db_path: str, frozen_clock: FrozenClock) -> None:
    m, llm = _llm_memory(db_path, frozen_clock, "duplicate")
    first = m.add("dark roast coffee", raw=True).added[0].id  # raw → no EXTRACT LLM call
    res = m.add("dark roast coffee beans", raw=True)  # band → DEDUP_BATCH says duplicate
    assert res.added == []
    assert res.deduped == [first]
    assert TaskTag.DEDUP_BATCH in llm.calls


def test_ambiguous_band_llm_unrelated_keeps_both(db_path: str, frozen_clock: FrozenClock) -> None:
    m, llm = _llm_memory(db_path, frozen_clock, "unrelated")
    m.add("dark roast coffee", raw=True)
    res = m.add("dark roast coffee beans", raw=True)  # band → DEDUP_BATCH says unrelated
    assert len(res.added) == 1
    assert res.deduped == []
    assert TaskTag.DEDUP_BATCH in llm.calls


@pytest.mark.parametrize("relation", ["duplicate", "unrelated"])
def test_dedup_judge_only_runs_in_band(
    db_path: str, frozen_clock: FrozenClock, relation: str
) -> None:
    """Distinct facts (<0.82) never reach the LLM judge — no unscripted call raised."""
    m, llm = _llm_memory(db_path, frozen_clock, relation)
    m.add("dark roast coffee", raw=True)
    m.add("I drive a Ferrari 488 GTB", raw=True)  # <0.82 → no DEDUP_BATCH call
    assert TaskTag.DEDUP_BATCH not in llm.calls
