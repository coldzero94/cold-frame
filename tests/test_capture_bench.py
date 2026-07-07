"""Capture-quality benchmark gate — pins the anti-bloat claim for auto-capture so it can't regress.

The Layer-A salience filter must keep the durable facts while dropping the session noise, beating a
capture-everything baseline on precision. Assertions are FLOORS (>=), not locks, so a future
Layer-A tightening only improves them. Fully offline + deterministic.
"""

from __future__ import annotations

from cold_frame.eval.capture_bench import evaluate


def test_layer_a_recalls_the_durable_facts() -> None:
    # the durable first-person facts/decisions must survive the salience filter (few missed)
    r = evaluate()
    assert r["coldframe"]["recall"] >= 0.85


def test_layer_a_beats_capture_everything_on_precision() -> None:
    r = evaluate()
    cf, be = r["coldframe"], r["capture_everything"]
    # the anti-bloat win: Layer-A drops the noise, so its precision is well above the baseline's
    assert cf["precision"] > be["precision"]
    assert cf["precision"] >= 0.7
    # ...without sacrificing recall (it still keeps the durable facts the baseline does)
    assert cf["recall"] >= be["recall"] - 0.15
    # and it hoards less — fewer captured items than storing every user turn
    assert cf["captured"] < be["captured"]


def test_capture_bench_is_deterministic() -> None:
    assert evaluate() == evaluate()  # pure Layer-A decision, no RNG/clock → identical runs
