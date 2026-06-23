"""P6: agentic self-edit tools (create_fact / update_fact / supersede / forget).

All four converge on the ONE WriteCore (I15) — the same persist path as add/correct_memory.
These are the unit checks; the dedup+freshness-through-the-tool gate lives in the eval suites.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.eval.harness import load_suite, run_case
from cold_frame.exceptions import NoteNotFound, ToolError
from cold_frame.llm.base import HashEmbedder

from tests.conftest import FrozenClock

_DATASETS = Path(__file__).resolve().parents[1] / "cold_frame" / "eval" / "datasets"


def _mem(db_path: str, clock: FrozenClock) -> Memory:
    return Memory(db_path, embedder=HashEmbedder(), llm=None, clock=clock)  # offline


def test_create_fact_adds_and_auto_dedups(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    first = m.create_fact("I prefer dark roast coffee")
    assert len(first.added) == 1
    # an identical assertion (cosine 1.0 ≥ 0.93) auto-merges through the same commit — no dup
    again = m.create_fact("I prefer dark roast coffee")
    assert again.added == [] and again.deduped  # merged into the existing note
    assert len(m.list_active()) == 1


def test_update_fact_supersedes(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = m.create_fact("I work at Vessl").added[0].id
    res = m.update_fact(fid, "I work at Anthropic")
    assert res.archived == fid
    assert m.get(fid).status == "archived"  # old archived (revivable, I2)
    assert m.get(res.new.id).status == "active"
    assert m.search("Anthropic").hits[0].note.id == res.new.id


def test_supersede_tool(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = m.create_fact("the deploy script is deploy.sh").added[0].id
    res = m.supersede(fid, "the deploy script is now ship.sh")
    assert res.archived == fid and m.get(fid).status == "archived"
    assert m.get(res.new.id).content == "the deploy script is now ship.sh"


def test_memory_tools_specs(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    tools = m.memory_tools(m._default_scope)
    assert {t.name for t in tools} == {"create_fact", "update_fact", "supersede", "forget"}
    create = next(t for t in tools if t.name == "create_fact")
    assert create.input_schema["required"] == ["text"]


def test_apply_tool_dispatch(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    created = m.apply_tool("create_fact", {"text": "I use a standing desk"})
    fid = created["added"][0]  # type: ignore[index]

    updated = m.apply_tool("update_fact", {"id": fid, "text": "I use a sitting desk"})
    assert updated["archived"] == fid and updated["new"]

    new_id = updated["new"]
    sup = m.apply_tool("supersede", {"id": new_id, "text": "I use a treadmill desk"})
    assert sup["archived"] == new_id

    forgotten = m.apply_tool("forget", {"id": sup["new"]})
    assert forgotten["status"] == "archived"


def test_apply_tool_create_fact_returns_admission_fields(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m = _mem(db_path, frozen_clock)
    out = m.apply_tool("create_fact", {"text": "I like tea"})
    assert "held" in out and "blocked" in out  # the agent sees durability-gate/secret outcomes (I6)


def test_apply_tool_unknown_raises(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    with pytest.raises(ToolError, match="unknown self-edit tool"):  # ColdFrameError → MCP envelope
        m.apply_tool("delete_everything", {})


def test_apply_tool_missing_arg_raises_tool_error(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    with pytest.raises(ToolError, match="requires a non-empty"):
        m.apply_tool("create_fact", {})  # missing text
    with pytest.raises(ToolError, match="requires a non-empty"):
        m.apply_tool("update_fact", {"id": "x"})  # missing text


def test_apply_tool_memory_type_passthrough_and_validation(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m = _mem(db_path, frozen_clock)
    out = m.apply_tool("create_fact", {"text": "deploy via ship.sh", "memory_type": "procedural"})
    assert m.get(out["added"][0]).memory_type == "procedural"  # type: ignore[index]
    with pytest.raises(ToolError, match="invalid memory_type"):
        m.apply_tool("create_fact", {"text": "x", "memory_type": "bogus"})


def test_memory_delete_requires_force(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    fid = m.create_fact("a deletable fact").added[0].id
    with pytest.raises(ValueError, match="force=True"):
        m.delete(fid)  # safe default refuses → no accidental permanent loss
    assert m.get(fid).status == "active"  # untouched
    m.delete(fid, force=True)
    with pytest.raises(NoteNotFound):
        m.get(fid)  # permanently gone (not revivable, unlike forget)


def test_update_fact_unknown_id_raises(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    with pytest.raises(NoteNotFound):
        m.update_fact("nope", "x")
    with pytest.raises(NoteNotFound):  # forget tool on an unknown id surfaces not_found too
        m.apply_tool("forget", {"id": "ghost"})


# ── P6 GATE: dedup + freshness suites THROUGH the create_fact tool path (I15) ──
@pytest.mark.parametrize("suite_name", ["dedup", "freshness"])
def test_self_edit_path_matches_suite(suite_name: str) -> None:
    """The same golden cases, with `add` routed through create_fact, must still pass —
    proving the self-edit tool and add share the single WriteCore (I15)."""
    suite = load_suite(_DATASETS / f"{suite_name}.yaml")
    for case in suite.cases:
        report = run_case(case, via_tool=True)
        assert report.passed, f"{suite_name}:{case.id} via tool failed:\n  " + "\n  ".join(
            report.failures
        )
