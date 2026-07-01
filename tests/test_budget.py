"""Token-budget packer tests (P3 unit 2): cap respected, top-rank kept, non-empty guarantee."""

from __future__ import annotations

from cold_frame.api import Memory
from cold_frame.llm.tokens import HeuristicCounter

_FACTS = [
    "dark roast coffee preference in the morning",
    "I drive a red Ferrari 488 GTB on weekends",
    "my favorite programming language is Python",
    "I live in Seoul near the Han river",
]
_QUERY = "coffee Ferrari Python Seoul river"


def test_budget_none_returns_all_topk(memory: Memory) -> None:
    memory.add("dark roast coffee")
    res = memory.search("coffee")
    assert res.used_tokens is None  # no budget → no packing
    assert res.truncated is False


def test_budget_caps_tokens_and_keeps_top_rank(memory: Memory) -> None:
    for fact in _FACTS:
        memory.add(fact)
    full = memory.search(_QUERY, k=10)
    assert len(full.hits) >= 3  # several facts match

    c = HeuristicCounter()
    budget = c.count(full.hits[0].note.content) + c.count(full.hits[1].note.content)
    res = memory.search(_QUERY, k=10, token_budget=budget)

    assert res.used_tokens is not None
    assert res.used_tokens <= budget  # hard cap respected
    assert sum(c.count(h.note.content) for h in res.hits) == res.used_tokens
    assert res.hits[0].note.id == full.hits[0].note.id  # highest-rank fact preserved
    assert len(res.hits) < len(full.hits)  # lower-rank facts dropped by budget
    assert res.truncated is True  # ...and the withholding is flagged (caller knows facts dropped)


def test_budget_guarantees_nonempty_even_when_top_exceeds(memory: Memory) -> None:
    content = "a fairly long fact about dark roast coffee preferences in the early morning"
    memory.add(content)
    res = memory.search("coffee", token_budget=1)  # tiny budget < the top hit
    assert len(res.hits) == 1  # the top hit is still emitted (never an empty result)
    # HONEST reporting: used_tokens is the REAL emitted size (exceeds the budget in this bend), NOT
    # a capped lie of `budget`. truncated=True flags that the cap was blown to keep a result.
    real = HeuristicCounter().count(res.hits[0].note.content)
    assert res.used_tokens == real and real > 1
    assert res.truncated is True


def test_budget_zero_returns_empty(memory: Memory) -> None:
    memory.add("dark roast coffee")
    res = memory.search("coffee", token_budget=0)
    assert res.hits == []
    assert res.used_tokens == 0
    assert res.truncated is False
