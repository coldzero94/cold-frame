"""Extraction tests (P1 unit 4): offline naive (1 user msg = 1 fact) + scripted-LLM path.

Offline is the through-line for the gate suites; the scripted-LLM path proves the
deterministic durability + confidence gates (the LLM proposes, code disposes — I1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from cold_frame.llm.base import LLMResult, TaskTag
from cold_frame.models import Scope
from cold_frame.prompts.extract import ExtractedFact, ExtractionOutput
from cold_frame.write.extract import extract

from tests.conftest import FrozenClock, ScriptedLLM

OBS = datetime(2026, 6, 20, 9, 0, 0, tzinfo=UTC)  # observation time (earlier)
NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)  # clock.now() (transaction time)


def _ids() -> Callable[[], str]:
    seq = {"i": -1}

    def factory() -> str:
        seq["i"] += 1
        return f"id-{seq['i']}"

    return factory


# ── offline naive path ────────────────────────────────────────────────────────
def test_naive_extract_one_msg_one_fact() -> None:
    notes = extract(
        [{"role": "user", "content": "I prefer dark roast"}],
        llm=None,
        clock=FrozenClock(NOW),
        new_id=_ids(),
        observed_at=OBS,
        scope=Scope(),
    )
    assert len(notes) == 1
    n = notes[0]
    assert n.content == "I prefer dark roast"
    assert n.memory_type == "episodic"
    assert n.confidence == 0.5
    assert n.importance == 0.5
    assert n.valid_at == OBS  # valid time = when observed
    assert n.created_at == NOW  # transaction time = clock.now()
    assert n.status == "active"
    assert n.decay_S == 1.0
    assert not n.held_for_human and not n.quarantined  # 0.5 >= CONFIDENCE_FLOOR(0.4)
    assert len(n.sources) == 1
    s = n.sources[0]
    assert s.kind == "message" and s.role == "user"
    assert s.content_hash == hashlib.sha256(b"I prefer dark roast").hexdigest()
    assert s.observed_at == OBS


def test_naive_extract_accepts_str_input() -> None:
    notes = extract(
        "green tea over coffee",
        llm=None,
        clock=FrozenClock(NOW),
        new_id=_ids(),
        observed_at=OBS,
        scope=Scope(),
    )
    assert [n.content for n in notes] == ["green tea over coffee"]


def test_naive_extract_skips_non_user_messages() -> None:
    notes = extract(
        [
            {"role": "assistant", "content": "How can I help?"},
            {"role": "user", "content": "remember I like cats"},
        ],
        llm=None,
        clock=FrozenClock(NOW),
        new_id=_ids(),
        observed_at=OBS,
        scope=Scope(),
    )
    assert [n.content for n in notes] == ["remember I like cats"]


def test_naive_extract_ids_are_deterministic() -> None:
    notes = extract(
        [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
        llm=None,
        clock=FrozenClock(NOW),
        new_id=_ids(),
        observed_at=OBS,
        scope=Scope(),
    )
    assert [n.id for n in notes] == ["id-0", "id-1"]


# ── scripted-LLM path: durability + confidence gates ───────────────────────────
def test_extract_llm_path_applies_gates() -> None:
    script = {
        TaskTag.EXTRACT: LLMResult(
            parsed=ExtractionOutput(
                facts=[
                    ExtractedFact(
                        text="User prefers dark roast coffee",
                        memory_type="semantic",
                        keywords=["coffee", "roast"],
                        confidence=0.9,
                        importance=0.8,
                        durability="durable",
                    ),
                    ExtractedFact(  # ephemeral + low importance → DROPPED (durability gate)
                        text="User said hello this morning",
                        memory_type="episodic",
                        confidence=0.7,
                        importance=0.2,
                        durability="ephemeral",
                    ),
                    ExtractedFact(  # low confidence — kept by extract; HELD later by WriteCore
                        text="User might be stressed",
                        memory_type="semantic",
                        confidence=0.3,
                        importance=0.6,
                        durability="durable",
                    ),
                ]
            )
        )
    }
    llm = ScriptedLLM(script)
    notes = extract(
        [{"role": "user", "content": "..."}],
        llm=llm,
        clock=FrozenClock(NOW),
        new_id=_ids(),
        observed_at=OBS,
        scope=Scope(),
    )

    assert TaskTag.EXTRACT in llm.calls
    assert len(notes) == 2  # ephemeral chatter dropped
    durable, low = notes
    assert durable.content == "User prefers dark roast coffee"
    assert durable.memory_type == "semantic"
    assert durable.confidence == 0.9
    assert durable.keywords == ["coffee", "roast"]
    assert not durable.held_for_human and not durable.quarantined
    # extraction applies only the DURABILITY gate; the confidence/consent HOLD is centralized in
    # WriteCore._consent_gate (I15) — so extract() no longer marks the low-conf fact held here (the
    # held-below-gate behavior is tested end-to-end via add() in test_consent.py).
    assert low.confidence == 0.3
    assert not low.held_for_human and not low.quarantined


def test_derive_tags_offline_and_via_add() -> None:
    from cold_frame.write.extract import derive_tags

    tags = derive_tags("I deploy the backend with ship.sh every morning", "episodic")
    assert tags[0] == "episodic"  # the memory_type leads (coarse category)
    assert "deploy" in tags and "backend" in tags  # salient terms
    assert "with" not in tags and "every" not in tags  # stopwords excluded
    assert len(tags) <= 6 and len(tags) == len(set(tags))  # capped + deduped


def test_add_populates_tags(memory: object) -> None:
    res = memory.add("I prefer dark roast coffee in the morning")  # type: ignore[attr-defined]
    note = res.added[0]
    assert note.tags and note.tags[0] == "episodic"  # tags are no longer empty (was dead)
    assert any(t in note.tags for t in ("dark", "roast", "coffee", "morning"))
