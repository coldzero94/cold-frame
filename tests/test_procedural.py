"""Procedural memory tests (P5): the f-string var-healer (SPEC §7 / prompts §5.3)."""

from __future__ import annotations

import pytest
from cold_frame.exceptions import VarHealerError
from cold_frame.procedural.optimize import heal_vars


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
