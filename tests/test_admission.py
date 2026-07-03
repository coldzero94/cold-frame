"""Admission tests (v1, D25): deterministic secret-BLOCK before disk (I6).

Obvious secrets are blocked pre-disk (deterministic, no LLM). The ambiguous [4.0,4.5) entropy band
no longer gates STORAGE — the local-only LLM tiebreak was removed (v1 scope) — it only feeds
write/extract's remote-egress guard now. Blocked output NEVER carries the secret content (I6/I16).
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import SecretBlocked
from cold_frame.llm.base import HashEmbedder, LLMResult, TaskTag
from cold_frame.models import ConflictVerdict
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


# ── ambiguous entropy band [4.0, 4.5): a detection fn (feeds the remote-egress guard) ──
# The band NO LONGER gates storage — the local-only LLM tiebreak was removed (v1 scope: no local LLM
# ships, and a remote LLM made it fail-closed-BLOCK legit facts). 32 chars, entropy ~4.25 → in band.
_AMBIG = "abcdefghijklmnopqrstabcdefghijkl"
_AMBIG_TEXT = f"my deploy id is {_AMBIG} ok"


def test_ambiguous_spans_detects_the_band() -> None:
    assert ambiguous_spans(_AMBIG_TEXT)  # the token is in [4.0, 4.5)
    assert scan_secret(_AMBIG_TEXT) is None  # ...but NOT a definite secret
    assert ambiguous_spans("just a short normal sentence about coffee") == []


def test_ambiguous_band_proceeds_on_storage(db_path: str, frozen_clock: FrozenClock) -> None:
    # STORAGE no longer gates the ambiguous band: the removed LLM tiebreak used to fail-closed-BLOCK
    # here (the worker footgun). A long high-entropy token now proceeds; real secrets are still
    # caught by scan_secret (vendor patterns + >=4.5 entropy). Even a REMOTE LLM is never called.
    llm = ScriptedLLM({}, is_local=False)  # empty script → any complete() call would AssertionError
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    res = m.add(_AMBIG_TEXT, raw=True)
    assert res.added and not res.blocked  # proceeds — no tiebreak, no fail-closed block
    assert llm.calls == []  # admission made ZERO LLM calls (the tiebreak apparatus is gone)


def test_ambiguous_band_lower_boundary_excludes_hashes_uuids() -> None:
    # below the 4.0 floor: a hex SHA and a dashed UUID are NOT routed to the tiebreak (no LLM noise)
    sha = "a3f5c8e1b2d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f2a4c6e8b0d2f4a6"  # 64 hex, ~3.64
    uuid = "550e8400-e29b-41d4-a716-446655440000"  # dashed, ~3.39 (hyphens are in the _TOKEN class)
    assert ambiguous_spans(f"commit {sha}") == []
    assert ambiguous_spans(f"trace id {uuid}") == []


# ── extraction leg: raw chat must not reach a REMOTE extractor if it holds a secret ──
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


# ── judge legs: the dedup/conflict judge must also withhold a secret/ambiguous span from a
# REMOTE endpoint (I7 egress consistency — the extract leg's guard, applied to the judge calls) ──
_AMB = "C3J27XDCG2LmlZGEONYlgCtjC3J27XDCG2Lm"  # 36-char token, entropy in the ambiguous band


def test_remote_dedup_judge_never_receives_ambiguous_span(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # in-band near-dup (cosine ~0.894) carrying an ambiguous token + a REMOTE LLM → the dedup judge
    # is skipped (fail-closed: kept distinct), the span never ships to the remote endpoint.
    llm = ScriptedLLM({}, is_local=False)  # empty script → any complete() call AssertionErrors
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    m.add(f"dark roast coffee {_AMB}", raw=True)
    res = m.add(f"dark roast coffee beans {_AMB}", raw=True)
    assert TaskTag.DEDUP_BATCH not in llm.calls  # never shipped to the remote judge
    assert len(res.added) == 1 and res.deduped == []  # fail-closed → kept distinct


def test_local_dedup_judge_may_receive_ambiguous_span(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    # the egress guard is REMOTE-only: a LOCAL judge may see the ambiguous span (never leaves box).
    verdict = ConflictVerdict(relation="unrelated", confidence=0.9)
    llm = ScriptedLLM({TaskTag.DEDUP_BATCH: LLMResult(parsed=verdict)}, is_local=True)
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    m.add(f"dark roast coffee {_AMB}", raw=True)
    m.add(f"dark roast coffee beans {_AMB}", raw=True)
    assert TaskTag.DEDUP_BATCH in llm.calls  # local judge IS invoked (in-band near-dup)
