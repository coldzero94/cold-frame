"""Admission tests (v1, D25): deterministic secret-BLOCK before disk (I6).

Obvious secrets are blocked pre-disk (deterministic); an AMBIGUOUS span is resolved by the
LOCAL-only I7 tiebreak (built, exercised here). CONFIDENCE-GATE/CONSENT + crypto-shred deferred.
The blocked output NEVER carries the secret content (I6/I16).
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import SecretBlocked
from cold_frame.llm.base import LLM, HashEmbedder, LLMResult, TaskTag
from cold_frame.prompts.admission import AdmissionVerdict
from cold_frame.prompts.extract import ExtractionOutput
from cold_frame.write.admission import ambiguous_spans, scan_secret

from tests.conftest import FrozenClock, ScriptedLLM

_AWS = "AKIA1234567890ABCDEF"
_OPENAI = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
_GH = "ghp_" + "a" * 36
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc...\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "text",
    [
        f"my aws key is {_AWS}",
        f"openai api key: {_OPENAI}",
        f"token {_GH}",
        _PEM,
        "password = hunter2longenough",
        "here is a blob a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ1234",  # 40-char high-entropy token
    ],
)
def test_scan_flags_obvious_secrets(text: str) -> None:
    verdict = scan_secret(text)
    assert verdict is not None
    reason, placeholder = verdict
    assert reason in ("secret", "credential")
    assert placeholder.startswith("[BLOCKED:")  # label only — never the matched content


@pytest.mark.parametrize(
    "text",
    [
        "I prefer dark roast coffee",
        "I switched jobs to Anthropic in 2026",
        "the deploy script is ship.sh",
        "my favorite number is 42",
    ],
)
def test_scan_passes_normal_text(text: str) -> None:
    assert scan_secret(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "the commit sha is a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",  # 40-hex git sha
        "session 550e8400-e29b-41d4-a716-446655440000 expired",  # a UUID
        "class ThisIsAVeryLongCamelCaseClassNameForTestingThings",  # long camelCase identifier
        "file src/components/widgets/forms/inputs/text_field_helper",  # path-like token
    ],
)
def test_scan_no_false_positive_on_ordinary_long_tokens(text: str) -> None:
    assert scan_secret(text) is None  # hashes / UUIDs / identifiers / paths are not secrets


def _mem(db_path: str, clock: FrozenClock) -> Memory:
    return Memory(db_path, embedder=HashEmbedder(), llm=None, clock=clock)


def test_add_blocks_secret_before_disk(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    res = m.add(f"my aws key is {_AWS}", raw=True)
    assert res.added == [] and res.blocked  # blocked, not stored (I6)
    assert _AWS not in res.blocked[0].placeholder  # the secret never leaks into the result
    assert m.list_active() == []  # nothing persisted
    assert m.search("aws key").hits == []  # not searchable
    # the secret is absent from the raw DB (not in notes content nor FTS)
    rows = m._store._conn.execute("SELECT content FROM notes").fetchall()
    assert all(_AWS not in r[0] for r in rows)


def test_create_fact_blocks_secret(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    res = m.create_fact(f"deploy token {_GH}")
    assert res.added == [] and res.blocked
    assert len(m.list_active()) == 0


def test_update_fact_with_secret_raises_and_keeps_old(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    m = _mem(db_path, frozen_clock)
    fid = m.create_fact("the old value is fine").added[0].id
    with pytest.raises(SecretBlocked):
        m.update_fact(fid, f"the new value is {_OPENAI}")
    assert m.get(fid).status == "active"  # the secret-bearing edit never landed
    assert m.get(fid).content == "the old value is fine"


def test_normal_add_still_works(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _mem(db_path, frozen_clock)
    res = m.add("I prefer dark roast coffee", raw=True)
    assert len(res.added) == 1 and not res.blocked  # admission is a pass-through for clean text


# ── I7: local-only admission tiebreak for an AMBIGUOUS span ────────────────────
# 32 chars, verified entropy 4.25 → in the [4.0, 4.5) ambiguous band (>=32 for the _TOKEN scanner;
# not a definite secret, not a plain hash/uuid)
_AMBIG = "abcdefghijklmnopqrstabcdefghijkl"
_AMBIG_TEXT = f"my deploy id is {_AMBIG} ok"


def test_ambiguous_spans_detects_the_band() -> None:
    assert ambiguous_spans(_AMBIG_TEXT)  # the token is in [4.0, 4.5)
    assert scan_secret(_AMBIG_TEXT) is None  # ...but NOT a definite secret
    assert ambiguous_spans("just a short normal sentence about coffee") == []


def _tiebreak_mem(db_path, clock, *, verdict, is_local):  # type: ignore[no-untyped-def]
    script = {TaskTag.ADMISSION_TIEBREAK: LLMResult(parsed=verdict)}
    return Memory(
        db_path, embedder=HashEmbedder(), llm=ScriptedLLM(script, is_local=is_local), clock=clock
    )


def test_ambiguous_offline_no_llm_is_allowed(db_path: str, frozen_clock: FrozenClock) -> None:
    # I5: with no LLM there is no tiebreak — the deterministic gate stands, ambiguous proceeds.
    res = _mem(db_path, frozen_clock).add(_AMBIG_TEXT, raw=True)
    assert res.added and not res.blocked


def test_ambiguous_local_llm_says_secret_blocks(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _tiebreak_mem(
        db_path, frozen_clock, verdict=AdmissionVerdict(is_secret=True), is_local=True
    )
    res = m.add(_AMBIG_TEXT, raw=True)
    assert not res.added and res.blocked and res.blocked[0].placeholder == "[BLOCKED:ambiguous]"


def test_ambiguous_local_llm_says_clean_allows(db_path: str, frozen_clock: FrozenClock) -> None:
    m = _tiebreak_mem(
        db_path, frozen_clock, verdict=AdmissionVerdict(is_secret=False), is_local=True
    )
    res = m.add(_AMBIG_TEXT, raw=True)
    assert res.added and not res.blocked


def test_ambiguous_remote_llm_fails_closed_and_never_sends(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # I7: a non-local LLM must NOT receive the span — assert_local_for raises BEFORE complete().
    llm = ScriptedLLM({}, is_local=False)  # empty script → any complete() call would AssertionError
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    res = m.add(_AMBIG_TEXT, raw=True)
    assert not res.added and res.blocked  # fail closed
    assert res.blocked[0].reason == "ambiguous"  # NOT "secret" — it was unverifiable, not confirmed
    assert res.blocked[0].placeholder == "[BLOCKED:ambiguous_remote_llm]"
    assert TaskTag.ADMISSION_TIEBREAK not in llm.calls  # the span never reached the remote model


def test_ambiguous_band_lower_boundary_excludes_hashes_uuids() -> None:
    # below the 4.0 floor: a hex SHA and a dashed UUID are NOT routed to the tiebreak (no LLM noise)
    sha = "a3f5c8e1b2d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f2a4c6e8b0d2f4a6"  # 64 hex, ~3.64
    uuid = "550e8400-e29b-41d4-a716-446655440000"  # dashed, ~3.39 (hyphens are in the _TOKEN class)
    assert ambiguous_spans(f"commit {sha}") == []
    assert ambiguous_spans(f"trace id {uuid}") == []


def test_ambiguous_unparseable_verdict_fails_closed(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # local LLM returns no parsed verdict → can't confirm safe → BLOCK (reason=ambiguous)
    m = _tiebreak_mem(db_path, frozen_clock, verdict=None, is_local=True)
    res = m.add(_AMBIG_TEXT, raw=True)
    assert not res.added and res.blocked and res.blocked[0].reason == "ambiguous"


def test_supersede_path_runs_the_tiebreak(db_path: str, frozen_clock: FrozenClock) -> None:
    # the tiebreak guards commit_supersede (correct_memory), not just the add path (I15)
    m = _tiebreak_mem(
        db_path, frozen_clock, verdict=AdmissionVerdict(is_secret=True), is_local=True
    )
    fid = m.add("a clean baseline fact", raw=True).added[0].id
    with pytest.raises(SecretBlocked):
        m.correct_memory(fid, _AMBIG_TEXT)
    assert m.get(fid).content == "a clean baseline fact"  # the old fact is untouched (no partial)


def test_ambiguous_loop_checks_every_span(db_path: str, frozen_clock: FrozenClock) -> None:
    # multiple ambiguous spans → ALL are tiebroken (a spans[0]-only loop would miss a later secret)
    m = _tiebreak_mem(
        db_path, frozen_clock, verdict=AdmissionVerdict(is_secret=False), is_local=True
    )
    ambig2 = "abcdefghijklmnopqrsabcdefghijklmnop"  # 2nd distinct >=32-char token in [4.0,4.5)
    res = m.add(f"ids {_AMBIG} and {ambig2}", raw=True)
    assert res.added  # the local LLM cleared both
    assert m._write._llm.calls.count(TaskTag.ADMISSION_TIEBREAK) == 2  # the loop checked BOTH spans


def test_unscripted_local_tiebreak_propagates_not_silently_blocks(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # an undeclared local-LLM call MUST stay a hard failure (CLAUDE.md §2 / I16) — the tiebreak's
    # except must NOT swallow ScriptedLLM's AssertionError into a silent "ambiguous" block.
    m = Memory(  # local, but ADMISSION_TIEBREAK NOT scripted
        db_path, embedder=HashEmbedder(), llm=ScriptedLLM({}, is_local=True), clock=frozen_clock
    )
    with pytest.raises(AssertionError):
        m.add(_AMBIG_TEXT, raw=True)


def test_tiebreak_provider_failure_fails_closed(db_path: str, frozen_clock: FrozenClock) -> None:
    # a genuine provider/transport error (NOT a harness/contract error) → fail CLOSED, not a crash
    class _RaisingLLM(LLM):
        name = "raising"

        @property
        def is_local(self) -> bool:
            return True

        def complete(self, **kw: object) -> LLMResult:  # type: ignore[override]
            raise RuntimeError("simulated provider outage")

    m = Memory(db_path, embedder=HashEmbedder(), llm=_RaisingLLM(), clock=frozen_clock)
    res = m.add(_AMBIG_TEXT, raw=True)
    assert not res.added and res.blocked
    assert res.blocked[0].reason == "ambiguous"
    assert res.blocked[0].placeholder == "[BLOCKED:ambiguous_tiebreak_error]"


# ── I7 extraction leg: raw chat must not reach a REMOTE extractor if it holds a secret ──────────
def test_remote_extractor_never_receives_secret_bearing_chat(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # The tiebreak is local-only, but EXTRACTION (chat→facts) can use a remote LLM. Raw chat with an
    # OBVIOUS secret must NOT go there — extract() falls back to LOCAL naive, admission then BLOCKs
    # the secret pre-disk, so it never leaves the box.
    llm = ScriptedLLM(
        {}, is_local=False
    )  # remote; empty script → any complete() call AssertionErrors
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    res = m.add(f"my aws key is {_AWS}")  # infer=True → the LLM-extraction path (NOT raw)
    assert (
        TaskTag.EXTRACT not in llm.calls
    )  # the secret-bearing chat never reached the remote model
    assert not res.added and res.blocked and res.blocked[0].reason == "secret"


def test_remote_extractor_receives_clean_chat(db_path: str, frozen_clock: FrozenClock) -> None:
    # the guard is NARROW: clean chat (no secret/ambiguous span) still uses the remote extractor
    script = {TaskTag.EXTRACT: LLMResult(parsed=ExtractionOutput(facts=[]))}
    m = Memory(
        db_path,
        embedder=HashEmbedder(),
        llm=ScriptedLLM(script, is_local=False),
        clock=frozen_clock,
    )
    m.add("I really like dark roast coffee in the morning")
    assert m._write._llm.calls == [TaskTag.EXTRACT]  # clean chat IS sent to the remote extractor


def test_local_extractor_may_receive_secret_bearing_chat(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # the guard is REMOTE-only: a LOCAL extractor may see the secret (admission blocks it pre-disk)
    script = {TaskTag.EXTRACT: LLMResult(parsed=ExtractionOutput(facts=[]))}
    m = Memory(
        db_path, embedder=HashEmbedder(), llm=ScriptedLLM(script, is_local=True), clock=frozen_clock
    )
    m.add(f"my aws key is {_AWS}")
    assert m._write._llm.calls == [
        TaskTag.EXTRACT
    ]  # local extraction may receive it (never leaves box)
