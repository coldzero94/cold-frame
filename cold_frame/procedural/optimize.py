"""ProceduralOptimizer — gradient diagnose → edit, with f-string var-healer (SPEC §7).

Leaf stub. Bodies raise ``NotImplementedError``; P5 fills them in (GRADIENT_DIAGNOSE
gate → GRADIENT_EDIT; preserve all f-string vars or raise ``VarHealerError``).
"""

from __future__ import annotations

import re

from cold_frame.api import Msg
from cold_frame.exceptions import VarHealerError
from cold_frame.llm.base import LLM, Clock
from cold_frame.models import ProceduralResult
from cold_frame.store.base import Store

_VAR = re.compile(r"\{([^{}]+)\}")  # an f-string slot {var} (no nested braces)
_TO_OPTIMIZE = re.compile(r"</?TO_OPTIMIZE>")


def heal_vars(current: str, improved: str) -> str:
    """Preserve every f-string var from ``current`` in ``improved`` (langmem var-healer, §7.3).

    Hard-fails (``VarHealerError``) if the edit dropped a required ``{var}``. Strips
    ``<TO_OPTIMIZE>`` markers and escapes any stray braces the edit introduced (so a new
    ``{var}`` the LLM invented becomes a literal, never an f-string KeyError) while keeping
    the required slots intact.
    """
    required = set(_VAR.findall(current))
    missing = sorted(v for v in required if "{" + v + "}" not in improved)
    if missing:
        raise VarHealerError(f"procedural edit dropped required variable(s): {missing}")

    text = _TO_OPTIMIZE.sub("", improved)
    masks: dict[str, str] = {}
    for i, var in enumerate(sorted(required)):  # mask required slots so they survive escaping
        token = f"\x00{i}\x00"
        masks[token] = "{" + var + "}"
        text = text.replace("{" + var + "}", token)
    text = text.replace("{", "{{").replace("}", "}}")  # escape stray (LLM-introduced) braces
    for token, original in masks.items():
        text = text.replace(token, original)
    return text


class ProceduralOptimizer:
    """Self-improving behavior directives via reflective gradient edits (D9)."""

    def __init__(self, store: Store, *, llm: LLM | None, clock: Clock) -> None:
        self._store = store
        self._llm = llm
        self._clock = clock

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        raise NotImplementedError

    def get_procedural(self, name: str) -> str:
        """Current behavior directive for ``name``; ``""`` if none."""
        raise NotImplementedError
