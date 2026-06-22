"""ProceduralOptimizer — gradient diagnose → edit, with f-string var-healer (SPEC §7).

Leaf stub. Bodies raise ``NotImplementedError``; P5 fills them in (GRADIENT_DIAGNOSE
gate → GRADIENT_EDIT; preserve all f-string vars or raise ``VarHealerError``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from cold_frame.exceptions import VarHealerError
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import Note, ProceduralResult, Scope, Source
from cold_frame.prompts.procedural import (
    GRADIENT_DIAGNOSE_SYSTEM,
    GRADIENT_EDIT_SYSTEM,
    DiagnoseOutput,
    EditOutput,
    build_diagnose_user,
    build_edit_user,
)
from cold_frame.store.base import Store

if TYPE_CHECKING:
    from cold_frame.api import Msg

_LIST_LIMIT = 10_000

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

    def __init__(
        self,
        store: Store,
        *,
        embedder: Embedder,
        llm: LLM | None,
        clock: Clock,
        new_id: Callable[[], str],
        scope: Scope | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._clock = clock
        self._new_id = new_id
        self._scope = scope or Scope()

    def _find(self, name: str) -> Note | None:
        for note in self._store.by_status(
            scope=self._scope, status="active", sort="recent", limit=_LIST_LIMIT
        ):
            if note.memory_type == "procedural" and note.context == name:
                return note
        return None

    def get_procedural(self, name: str) -> str:
        """Current behavior directive for ``name``; ``""`` if none."""
        note = self._find(name)
        return note.content if note is not None else ""

    def set_procedural(self, name: str, text: str) -> Note:
        """Register/replace a behavior directive (a ``procedural`` note keyed by ``context``)."""
        now = self._clock.now()
        existing = self._find(name)
        if existing is not None:
            updated = existing.model_copy(update={"content": text, "version": existing.version + 1})
            self._store.update_note(
                updated, update_type="manual", emb=self._embedder.embed_one(text)
            )
            return updated
        note = Note(
            id=self._new_id(),
            content=text,
            memory_type="procedural",
            context=name,
            scope=self._scope,
            created_at=now,
            valid_at=now,
            sources=[
                Source(
                    kind="manual",
                    ref=f"procedural:{name}",
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                )
            ],
        )
        self._store.add_note(note, self._embedder.embed_one(text))
        return note

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        """diagnose (gate) → edit → var-heal → version (SPEC §7); LLM proposes, code disposes."""
        current = self._find(name)
        if current is None or self._llm is None:  # nothing to optimize / offline → no change
            text = current.content if current else ""
            version = current.version if current else 0
            return ProceduralResult(name=name, changed=False, text=text, version=version)

        traj = json.dumps(
            [{"role": str(m["role"]), "content": str(m["content"])} for m in trajectory]
        )
        diagnosis = self._llm.complete(
            task=TaskTag.GRADIENT_DIAGNOSE,
            system=GRADIENT_DIAGNOSE_SYSTEM,
            user=build_diagnose_user(current.content, traj, feedback),
            schema=DiagnoseOutput,
        ).parsed
        if not isinstance(diagnosis, DiagnoseOutput) or not diagnosis.warrants_adjustment:
            return ProceduralResult(  # drift gate: no concrete failure → leave it untouched
                name=name, changed=False, text=current.content, version=current.version
            )

        required = sorted(set(_VAR.findall(current.content)))
        edit = self._llm.complete(
            task=TaskTag.GRADIENT_EDIT,
            system=GRADIENT_EDIT_SYSTEM,
            user=build_edit_user(
                current.content, diagnosis.hypotheses, diagnosis.recommendations, required
            ),
            schema=EditOutput,
        ).parsed
        if not isinstance(edit, EditOutput):
            return ProceduralResult(
                name=name, changed=False, text=current.content, version=current.version
            )

        healed = heal_vars(
            current.content, edit.improved_prompt
        )  # VarHealerError → no version bump
        updated = current.model_copy(update={"content": healed, "version": current.version + 1})
        self._store.update_note(
            updated, update_type="feedback", emb=self._embedder.embed_one(healed)
        )
        return ProceduralResult(name=name, changed=True, text=healed, version=updated.version)
