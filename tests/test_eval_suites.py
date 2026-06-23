"""P1 gate: every golden case in datasets/*.yaml runs green (one pytest per case).

This is the P1 acceptance gate (CLAUDE.md §6 / eval §B.4): the extraction,
precision_at_k, and cross_scope suites must pass offline (HashEmbedder + llm=None).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cold_frame.eval.harness import Case, load_suite, run_case

_DATASETS = Path(__file__).resolve().parents[1] / "cold_frame" / "eval" / "datasets"


def _all_cases() -> list[tuple[str, Case]]:
    cases: list[tuple[str, Case]] = []
    for yml in sorted(_DATASETS.glob("*.yaml")):
        suite = load_suite(yml)
        for case in suite.cases:
            cases.append((f"{suite.suite}:{case.id}", case))
    return cases


_CASES = _all_cases()


def test_gate_suites_exist() -> None:
    suites = {cid.split(":", 1)[0] for cid, _ in _CASES}
    # P1 extraction/precision/cross_scope · P2 dedup/freshness · P3 token_budget ·
    # P4 forgetting · P6 self_edit (commit_supersede through the tool, I15)
    assert {
        "extraction",
        "precision_at_k",
        "cross_scope",
        "dedup",
        "freshness",
        "token_budget",
        "forgetting",
        "self_edit",
    } <= suites


@pytest.mark.parametrize("case", [c for _, c in _CASES], ids=[cid for cid, _ in _CASES])
def test_golden_case(case: Case) -> None:
    report = run_case(case)
    assert report.passed, f"{case.id} failed:\n  " + "\n  ".join(report.failures)
