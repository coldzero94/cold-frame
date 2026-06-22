"""Offline token counter tests (P3 unit 1)."""

from __future__ import annotations

from cold_frame.llm.tokens import HeuristicCounter, TokenCounter, get_token_counter


def test_heuristic_count_empty_and_nonempty() -> None:
    c = HeuristicCounter()
    assert c.count("") == 0
    assert c.count("hello world") == round(0.75 * (11 / 4) + 0.25 * 2)  # chars/4 + word blend
    assert c.count("x") == 1  # floor 1 for any non-empty text


def test_heuristic_count_scales_with_length() -> None:
    c = HeuristicCounter()
    assert c.count("one two three four five six") > c.count("one two")


def test_heuristic_truncate_by_char_budget() -> None:
    c = HeuristicCounter()
    assert c.truncate("abcdefghij", 1) == "abcd"  # 1 token ≈ 4 chars
    assert c.truncate("abc", 0) == ""


def test_get_token_counter_default_is_heuristic() -> None:
    c = get_token_counter()
    assert isinstance(c, HeuristicCounter)
    assert c.name == "heuristic-chars4"
    assert isinstance(c, TokenCounter)  # satisfies the protocol (runtime_checkable)


def test_get_token_counter_tiktoken_falls_back_when_absent() -> None:
    c = get_token_counter("tiktoken")  # tiktoken not in the core env → heuristic
    assert c.count("hello there") >= 1  # whichever counter, it counts
