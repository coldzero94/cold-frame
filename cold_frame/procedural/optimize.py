"""ProceduralOptimizer — gradient diagnose (drift gate) → edit → var-heal → version (SPEC §7).

Self-improving behavior directives: DIAGNOSE recommends a change only on concrete evidence
(else no edit), EDIT rewrites minimally, the deterministic var-healer preserves every
f-string variable (``VarHealerError`` on a drop), and ``Store.update_note`` versions the
``procedural`` note in place. The LLM proposes; code disposes (I1). A procedural write goes
through ``update_note`` directly — a deliberate WriteCore exception (I15) since a directive
is author-supplied, not an extracted fact.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from collections.abc import Callable
from typing import TYPE_CHECKING

from cold_frame.exceptions import VarHealerError
from cold_frame.llm.base import LLM, Clock, Embedder, TaskTag
from cold_frame.models import Note, ProceduralResult, Scope, Source
from cold_frame.observability import get_logger
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

_log = get_logger(__name__)

# An f-string slot, capturing the VARIABLE NAME (group 1); tolerates conversion (!r) +
# a format spec (:>10) so a cosmetic spec change is not mistaken for a dropped variable.
_SLOT = re.compile(r"\{([a-zA-Z_]\w*)(?:![rsa])?(?::[^{}]*)?\}")
_TO_OPTIMIZE = re.compile(r"</?TO_OPTIMIZE>")


def _slot_names(text: str) -> set[str]:
    # Strip ESCAPED doubled braces first: {{foo}} is a literal, not a slot. Without this, a
    # previously-healed {{foo}} is re-read as a required variable {foo} → spurious VarHealerError +
    # brace accumulation ({{foo}}→{{{foo}}}) when healed content is fed back round-to-round (P5).
    unescaped = text.replace("{{", "").replace("}}", "")
    return {m.group(1) for m in _SLOT.finditer(unescaped)}


def heal_vars(current: str, improved: str) -> str:
    """Preserve every f-string variable from ``current`` in ``improved`` (langmem var-healer, §7.3).

    Hard-fails (``VarHealerError``) if the edit dropped a required variable (matched by NAME,
    so ``{x:>10}`` → ``{x:>8}`` is fine). Strips ``<TO_OPTIMIZE>`` markers; escapes stray
    braces the edit introduced — a slot whose name is NOT required becomes a literal, never an
    f-string KeyError — while leaving already-doubled ``{{ }}`` literals untouched.
    """
    required = _slot_names(current)
    missing = sorted(required - _slot_names(improved))
    if missing:
        raise VarHealerError(f"procedural edit dropped required variable(s): {missing}")

    text = _TO_OPTIMIZE.sub("", improved)
    try:
        return _heal_via_formatter(text, required)
    except ValueError:  # unbalanced braces → can't tokenize; fall back to the regex-mask escape
        return _heal_via_regex(text, required)


def _heal_via_formatter(text: str, required: set[str]) -> str:
    """Escape via Python's OWN format tokenizer: it cleanly separates literal ``{{``/``}}`` from
    real ``{field}`` slots (and nested specs), so adjacent brace runs like ``}}}`` are never
    mis-paired the way ordered ``str.replace`` does (which silently demoted a required slot)."""
    out: list[str] = []
    for literal, field, spec, conv in string.Formatter().parse(text):
        out.append(literal.replace("{", "{{").replace("}", "}}"))  # literal text stays literal
        if field is None:
            continue
        token = "{" + field + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "") + "}"
        # a required slot stays a LIVE slot, verbatim; anything else → escaped literal (no KeyError)
        out.append(token if field in required else token.replace("{", "{{").replace("}", "}}"))
    return "".join(out)


def _heal_via_regex(text: str, required: set[str]) -> str:
    """Fallback for UNBALANCED braces (which Formatter().parse can't tokenize). Preserves required
    vars + escapes stray braces. Protect already-doubled literals FIRST so a {{name}} literal isn't
    re-read as a {name} slot (the same corruption the formatter path avoids structurally)."""
    masks: dict[str, str] = {}

    def _protect(match: re.Match[str]) -> str:
        if match.group(1) not in required:
            return match.group(0)  # non-required slot → fall through to be escaped (literal)
        token = f"\x00{len(masks)}\x00"
        masks[token] = match.group(0)
        return token

    text = text.replace("{{", "\x01").replace("}}", "\x02")  # protect literals BEFORE slot matching
    text = _SLOT.sub(_protect, text)  # mask genuine single-brace required slots
    text = text.replace("{", "{{").replace("}", "}}")  # escape stray single braces
    text = text.replace("\x01", "{{").replace("\x02", "}}")  # restore the original {{ }} literals
    for token, slot in masks.items():
        text = text.replace(token, slot)
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
        # targeted exact-scope SQL lookup — never a recency-bounded Python scan (which could miss a
        # directive past the page and create a duplicate), and never bleeds a broader scope's note.
        return self._store.find_procedural(name, self._scope)

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
        if not isinstance(diagnosis, DiagnoseOutput):  # malformed parse ≠ healthy no-op → log it
            _log.warning("diagnose_parse_failed", extra={"directive": name, "task": "diagnose"})
            return ProceduralResult(
                name=name, changed=False, text=current.content, version=current.version
            )
        if not diagnosis.warrants_adjustment:
            return ProceduralResult(  # drift gate: no concrete failure → leave it untouched
                name=name, changed=False, text=current.content, version=current.version
            )

        required = sorted(_slot_names(current.content))
        edit = self._llm.complete(
            task=TaskTag.GRADIENT_EDIT,
            system=GRADIENT_EDIT_SYSTEM,
            user=build_edit_user(
                current.content, diagnosis.hypotheses, diagnosis.recommendations, required
            ),
            schema=EditOutput,
        ).parsed
        if not isinstance(edit, EditOutput):  # warranted but unusable edit → log, keep current
            _log.warning("edit_parse_failed", extra={"directive": name, "task": "edit"})
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
