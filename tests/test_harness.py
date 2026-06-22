"""Eval harness tests (P1 unit 7): doc-canonical schema, sync ScriptedLLM, offline runner."""

from __future__ import annotations

from pathlib import Path

import pytest
from cold_frame.eval.harness import (
    Case,
    EvalError,
    ExpectBlock,
    ExpectSearch,
    LlmScriptEntry,
    ScriptedLLM,
    Step,
    Suite,
    load_suite,
    run_case,
    run_suite,
)
from cold_frame.llm.base import TaskTag
from cold_frame.prompts.extract import ExtractionOutput

_YAML = """
suite: smoke
embedder: hash
cases:
  - id: add-and-recall
    description: offline add then search recalls it
    steps:
      - op: add
        at: "2026-01-02T09:00:00Z"
        scope: {user_id: alice}
        text: "I prefer dark roast coffee"
    expect:
      notes:
        - where: {content_like: "dark roast"}
          status: active
          memory_type: episodic
      search:
        - query: "coffee"
          scope: {user_id: alice}
          expect_top_content_like: "dark roast"
"""


# ── schema: doc-canonical §B.1 ────────────────────────────────────────────────
def test_load_canonical_yaml(tmp_path: Path) -> None:
    p = tmp_path / "smoke.yaml"
    p.write_text(_YAML, encoding="utf-8")
    suite = load_suite(p)
    assert isinstance(suite, Suite)
    assert suite.suite == "smoke"
    assert suite.embedder == "hash"
    case = suite.cases[0]
    assert case.id == "add-and-recall"
    assert case.steps[0].op == "add"
    assert case.steps[0].scope is not None and case.steps[0].scope.user_id == "alice"
    assert case.expect.search[0].expect_top_content_like == "dark roast"


def test_load_missing_file_raises() -> None:
    with pytest.raises(EvalError):
        load_suite("/no/such/suite.yaml")


# ── runner: offline case runs green through Memory ────────────────────────────
def test_run_offline_case_passes() -> None:
    case = Case(
        id="c1",
        steps=[Step(op="add", scope=None, text="I prefer dark roast coffee")],
        expect=ExpectBlock(
            search=[ExpectSearch(query="coffee", expect_top_content_like="dark roast")]
        ),
    )
    report = run_case(case)
    assert report.passed, report.failures


def test_run_suite_aggregates(tmp_path: Path) -> None:
    p = tmp_path / "smoke.yaml"
    p.write_text(_YAML, encoding="utf-8")
    rep = run_suite(p)
    assert rep.ok
    assert rep.total == 1 and rep.passed == 1
    assert rep.metrics["pass_rate"] == 1.0


def test_run_case_reports_failure() -> None:
    case = Case(
        id="bad",
        steps=[Step(op="add", text="I like green tea")],
        expect=ExpectBlock(
            search=[ExpectSearch(query="green tea", expect_top_content_like="coffee")]
        ),
    )
    report = run_case(case)
    assert not report.passed
    assert any("coffee" in f for f in report.failures)


# ── ScriptedLLM (sync, ordered-with-fallback) ─────────────────────────────────
def test_scripted_llm_unmatched_raises() -> None:
    llm = ScriptedLLM([])
    with pytest.raises(EvalError):
        llm.complete(task=TaskTag.EXTRACT, system="", user="anything")


def test_scripted_llm_contains_consumes_and_any_is_reusable() -> None:
    script = [
        LlmScriptEntry(task=TaskTag.EXTRACT, match={"contains": "alpha"}, returns={"facts": []}),
        LlmScriptEntry(task=TaskTag.EXTRACT, match={"any": True}, returns={"facts": []}),
    ]
    llm = ScriptedLLM(script)
    # specific match wins + is consumed
    r1 = llm.complete(task=TaskTag.EXTRACT, system="", user="alpha here", schema=ExtractionOutput)
    assert isinstance(r1.parsed, ExtractionOutput)
    # consumed → next "alpha" falls through to the reusable any:true entry
    r2 = llm.complete(task=TaskTag.EXTRACT, system="", user="alpha again", schema=ExtractionOutput)
    assert isinstance(r2.parsed, ExtractionOutput)
    # any:true serves repeatedly
    r3 = llm.complete(task=TaskTag.EXTRACT, system="", user="zzz", schema=ExtractionOutput)
    assert isinstance(r3.parsed, ExtractionOutput)
    assert [c["task"] for c in llm.calls] == ["extract", "extract", "extract"]
