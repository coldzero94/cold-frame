"""Procedural memory tests (P5): the f-string var-healer (SPEC §7 / prompts §5.3)."""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import VarHealerError
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.models import Scope
from cold_frame.procedural.optimize import heal_vars
from cold_frame.prompts.procedural import DiagnoseOutput, EditOutput

from tests.conftest import FrozenClock, ScriptedLLM


def test_preserves_required_vars() -> None:
    healed = heal_vars(
        "Address the user as {user_name}.", "Always address the user as {user_name}."
    )
    assert "{user_name}" in healed
    assert healed == "Always address the user as {user_name}."


def test_dropped_var_hard_fails() -> None:
    with pytest.raises(VarHealerError):
        heal_vars("Greet {user_name} warmly.", "Greet the user warmly.")  # {user_name} dropped


def test_stray_brace_introduced_by_edit_is_escaped() -> None:
    # the edit invented a NEW {tools} slot not in the original → neutralize it (escape), keep {name}
    healed = heal_vars("Help {name}.", "Help {name} using {tools}.")
    assert "{name}" in healed
    assert "{{tools}}" in healed  # escaped → literal, never an f-string KeyError


def test_multiple_vars_in_any_order() -> None:
    healed = heal_vars("{greeting}, {name}!", "{name} — {greeting}!")
    assert "{name}" in healed and "{greeting}" in healed


def test_to_optimize_markers_stripped() -> None:
    healed = heal_vars("Reply to {name}.", "<TO_OPTIMIZE>Reply politely to {name}.</TO_OPTIMIZE>")
    assert "TO_OPTIMIZE" not in healed
    assert "{name}" in healed


def test_healed_prompt_is_format_safe() -> None:
    # the whole point: the healed prompt must .format() with exactly the required vars
    healed = heal_vars(
        "Hi {name}, see {tool}.", "Hi {name}, please use {tool} and {{literal}} text."
    )
    assert healed.format(name="Coby", tool="search")  # no KeyError / ValueError


def test_format_spec_var_not_dropped() -> None:
    # a cosmetic spec change ({count:>5} → {count:>3}) must NOT read as a dropped variable
    healed = heal_vars("Show {count:>5} items.", "Display {count:>3} items.")
    assert "{count" in healed
    assert healed.format(count=7) == "Display   7 items."


def test_preescaped_literal_brace_stays_single() -> None:
    # the edit already escaped a literal brace ({{literal}}) → don't double-escape it
    healed = heal_vars("Hi {name}.", "Hi {name}, write {{literal}} verbatim.")
    assert healed.format(name="Coby") == "Hi Coby, write {literal} verbatim."


# ── optimize_prompt: diagnose gate → edit → heal → version ────────────────────
def _proc_memory(
    db_path: str, clock: FrozenClock, diagnose: DiagnoseOutput, edit: EditOutput | None = None
) -> Memory:
    script = {TaskTag.GRADIENT_DIAGNOSE: LLMResult(parsed=diagnose)}
    if edit is not None:
        script[TaskTag.GRADIENT_EDIT] = LLMResult(parsed=edit)
    return Memory(db_path, embedder=HashEmbedder(), llm=ScriptedLLM(script), clock=clock)


def test_set_and_get_procedural(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock)
    m.set_procedural("tone", "Reply to {user} in English.")
    assert m.get_procedural("tone") == "Reply to {user} in English."
    assert m.get_procedural("missing") == ""  # absent → empty


def test_optimize_no_change_when_not_warranted(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _proc_memory(db_path, frozen_clock, DiagnoseOutput(warrants_adjustment=False))
    m.set_procedural("tone", "Reply to {user} in English.")
    res = m.optimize_prompt("tone", [{"role": "user", "content": "hi"}], "looked fine")
    assert res.changed is False  # drift gate: no concrete failure → untouched
    assert res.text == "Reply to {user} in English." and res.version == 1


def test_optimize_edits_heals_and_versions(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _proc_memory(
        db_path,
        frozen_clock,
        DiagnoseOutput(warrants_adjustment=True, recommendations="be warmer"),
        EditOutput(improved_prompt="Warmly reply to {user} in English."),
    )
    m.set_procedural("tone", "Reply to {user} in English.")
    res = m.optimize_prompt("tone", [{"role": "user", "content": "hi"}], "too terse")
    assert res.changed is True
    assert res.text == "Warmly reply to {user} in English." and res.version == 2
    assert m.get_procedural("tone") == "Warmly reply to {user} in English."  # persisted


def test_optimize_dropped_var_raises_and_keeps_old(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _proc_memory(
        db_path,
        frozen_clock,
        DiagnoseOutput(warrants_adjustment=True, recommendations="simplify"),
        EditOutput(improved_prompt="Reply in English."),  # dropped {user}!
    )
    m.set_procedural("tone", "Reply to {user} in English.")
    with pytest.raises(VarHealerError):
        m.optimize_prompt("tone", [{"role": "user", "content": "hi"}], "x")
    # heal_vars raises BEFORE any write, so the stored note is never touched (no partial edit)
    assert m.get_procedural("tone") == "Reply to {user} in English."


def test_optimize_absent_prompt_is_no_change(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _proc_memory(db_path, frozen_clock, DiagnoseOutput(warrants_adjustment=True))
    res = m.optimize_prompt("nope", [], "")
    assert res.changed is False and res.version == 0


def test_set_procedural_replace_bumps_version(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock)
    m.set_procedural("tone", "First {u}.")
    n2 = m.set_procedural("tone", "Second {u}.")
    assert n2.version == 2  # REPLACE routes through update_note (version++)
    assert m.get_procedural("tone") == "Second {u}."


def test_optimize_offline_existing_is_live_noop(db_path: str, frozen_clock: FrozenClock) -> None:
    m = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock)  # I5 offline
    m.set_procedural("tone", "Reply to {u}.")
    res = m.optimize_prompt("tone", [], "fb")
    assert res.changed is False
    assert res.text == "Reply to {u}." and res.version == 1  # live version, not 0


def test_optimize_malformed_diagnose_is_safe_noop(db_path: str, frozen_clock: FrozenClock) -> None:
    llm = ScriptedLLM({TaskTag.GRADIENT_DIAGNOSE: LLMResult(parsed=None)})  # unparseable
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    m.set_procedural("tone", "Reply to {u}.")
    res = m.optimize_prompt("tone", [{"role": "user", "content": "hi"}], "fb")
    assert res.changed is False and res.version == 1  # malformed parse → safe no-op, no write
    assert m.get_procedural("tone") == "Reply to {u}."


def test_optimize_malformed_edit_is_safe_noop(db_path: str, frozen_clock: FrozenClock) -> None:
    llm = ScriptedLLM(
        {
            TaskTag.GRADIENT_DIAGNOSE: LLMResult(parsed=DiagnoseOutput(warrants_adjustment=True)),
            TaskTag.GRADIENT_EDIT: LLMResult(parsed=None),  # warranted, but unusable edit
        }
    )
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    m.set_procedural("tone", "Reply to {u}.")
    res = m.optimize_prompt("tone", [{"role": "user", "content": "hi"}], "fb")
    assert res.changed is False and res.version == 1
    assert m.get_procedural("tone") == "Reply to {u}."


def test_procedural_lookup_is_scope_exact(db_path: str, frozen_clock: FrozenClock) -> None:
    # write under a NARROW scope; a BROADER default scope must not bleed/edit that directive
    narrow = Memory(
        db_path,
        embedder=HashEmbedder(),
        llm=None,
        clock=frozen_clock,
        default_scope=Scope(agent_id="agent-a"),
    )
    narrow.set_procedural("tone", "Narrow {u}.")
    broad = Memory(db_path, embedder=HashEmbedder(), llm=None, clock=frozen_clock)  # Scope()
    assert broad.get_procedural("tone") == ""  # scope isolation: no cross-scope match
    assert narrow.get_procedural("tone") == "Narrow {u}."  # its own scope still resolves


def test_heal_vars_idempotent_on_escaped_literal() -> None:
    # a previously-healed {{foo}} (escaped literal) must NOT be re-read as a required var on the
    # next round: no spurious VarHealerError, and no brace accumulation ({{foo}}→{{{foo}}}).
    # current="Hello {user}" (required={user}); the edit adds a non-required {foo} → escaped literal
    once = heal_vars("Hello {user}", "Hello {user}, see {foo}")
    assert once == "Hello {user}, see {{foo}}"
    # round 2: feed the healed text back as `current`; dropping the {{foo}} LITERAL must NOT raise
    # (foo was never a required var — the fix strips {{}} before slot detection)
    twice = heal_vars(once, "Hello {user}")
    assert twice == "Hello {user}"
    # and re-healing identical content is stable (no {{foo}}→{{{foo}}} accumulation)
    assert heal_vars(once, once) == once


def test_heal_vars_still_catches_a_real_dropped_variable() -> None:
    with pytest.raises(VarHealerError):
        heal_vars("Hello {user}", "Hello there")  # a genuine single-brace slot was dropped
