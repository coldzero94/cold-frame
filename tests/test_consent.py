"""Admission confidence-gate + opt-in consent hold (D25 CONFIDENCE-GATE/CONSENT).

Centralized in the single WriteCore admission path (I15): a candidate below the configurable
``confidence_gate`` — or ANY candidate when ``require_consent`` is on — is held for human approval
(quarantined, out of default search) instead of auto-admitted. Approve via the Triage queue.
Defaults preserve prior behavior: gate=0.4, require_consent=False.
"""

from __future__ import annotations

from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.prompts.extract import ExtractedFact, ExtractionOutput

from tests.conftest import FrozenClock, ScriptedLLM


def _mem(db_path: str, clock: FrozenClock, **kw: object) -> Memory:
    return Memory(db_path, embedder=HashEmbedder(), llm=None, clock=clock, **kw)  # type: ignore[arg-type]


def test_default_admits_normal_confidence(db_path: str, frozen_clock: FrozenClock) -> None:
    # default gate (0.4) + no consent → a naive note (confidence 0.5) is admitted, not held.
    res = _mem(db_path, frozen_clock).add("I prefer dark roast coffee")
    assert res.added and not res.held


def test_require_consent_holds_every_new_memory(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock, require_consent=True)
    res = m.add("I prefer dark roast coffee")  # clean, confidence 0.5 — still held for consent
    assert not res.added and res.held
    assert res.held[0].triage_reason == "consent"
    assert res.held[0].quarantined is True
    assert m.search("coffee").hits == []  # quarantined → excluded from default search (I14)
    assert any(t.note.id == res.held[0].id for t in m.triage_queue())  # visible in Triage
    m.resolve_triage(res.held[0].id, action="keep")  # explicit approval promotes it
    assert m.get(res.held[0].id).status == "active" and not m.get(res.held[0].id).held_for_human
    assert m.search("coffee").hits  # now searchable


def test_confidence_gate_holds_below_threshold(db_path: str, frozen_clock: FrozenClock) -> None:
    # raise the gate above the naive 0.5 confidence → the note is held with reason low_confidence.
    m = _mem(db_path, frozen_clock, confidence_gate=0.6)
    res = m.add("some passing remark")  # naive confidence 0.5 < 0.6
    assert not res.added and res.held
    assert res.held[0].triage_reason == "low_confidence"


def test_lowering_the_gate_admits_low_confidence_facts(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # the gate is authoritative in BOTH directions — centralized in WriteCore, NOT the old hardcoded
    # 0.4 floor in extract.py. Lowering to 0.2 admits a confidence-0.3 fact the default (0.4) holds.
    fact = ExtractedFact(
        text="a low-stakes aside", memory_type="semantic", confidence=0.3, durability="durable"
    )
    script = {TaskTag.EXTRACT: LLMResult(parsed=ExtractionOutput(facts=[fact]))}
    m = Memory(
        db_path,
        embedder=HashEmbedder(),
        llm=ScriptedLLM(script, is_local=True),
        clock=frozen_clock,
        confidence_gate=0.2,
    )
    res = m.add("tell me an aside")
    assert res.added and not res.held  # 0.3 >= gate 0.2 → admitted (default 0.4 would hold it)


def test_require_consent_does_not_block_explicit_correction(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # correct_memory is explicit user intent (implicit consent) → it goes through commit_supersede,
    # which the consent gate does NOT touch. A correction lands active even under require_consent.
    m = _mem(db_path, frozen_clock, require_consent=True)
    held = m.add("the original note").held[0].id
    m.resolve_triage(held, action="keep")  # approve the base note
    res = m.correct_memory(held, "the corrected note")
    assert res.new.status == "active" and not res.new.held_for_human  # not re-held
