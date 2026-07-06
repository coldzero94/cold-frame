"""Moat benchmark gate — pins the differentiation claim so it can't silently regress.

The deterministic engine's win over an append-everything + cosine baseline is a load-bearing product
claim; if a refactor ever broke supersession's search exclusion, these turn red instead of the
benchmark quietly lying. Fully offline (HashEmbedder + a fixed clock, no LLM/network).
"""

from __future__ import annotations

from cold_frame.eval.moat_bench import evaluate, format_report
from cold_frame.llm.base import HashEmbedder


def test_coldframe_never_surfaces_a_stale_belief() -> None:
    # the headline: a corrected fact is archived (I1/I2), so a superseded belief is returned NEVER.
    r = evaluate(HashEmbedder())
    assert r["coldframe"]["stale_rate"] == 0.0


def test_coldframe_beats_the_naive_baseline_on_the_moat_metrics() -> None:
    r = evaluate(HashEmbedder())
    cf, nv = r["coldframe"], r["naive"]
    # naive keeps both versions active, so it DOES surface stale beliefs (and often)
    assert nv["stale_rate"] > cf["stale_rate"]
    assert nv["stale_rate"] >= 0.5
    # bounded active set vs unbounded growth (every correction adds a row in the naive store)
    assert cf["active"] < nv["active"]
    # and coldframe still recalls the CURRENT belief at least as well (no stale row competing)
    assert cf["current_recall"] >= nv["current_recall"]
    assert cf["current_recall"] >= 0.5


def test_benchmark_is_deterministic() -> None:
    # HashEmbedder + fixed clock + no RNG → byte-identical metrics across runs (reproducible claim)
    assert evaluate(HashEmbedder()) == evaluate(HashEmbedder())


def test_report_renders_a_table() -> None:
    out = format_report(evaluate(HashEmbedder()))
    assert "stale-belief rate" in out and "coldframe" in out and "naive" in out
