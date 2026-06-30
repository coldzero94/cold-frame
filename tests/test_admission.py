"""Admission tests (v1, D25): deterministic secret-BLOCK before disk (I6).

Lightweight scope — obvious secrets are blocked pre-disk; REDACT/purge/local-tiebreak (I7)
are deferred. The blocked output NEVER carries the secret content (I6/I16).
"""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import SecretBlocked
from cold_frame.llm.base import HashEmbedder
from cold_frame.write.admission import scan_secret

from tests.conftest import FrozenClock

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
from cold_frame.llm.base import LLMResult, TaskTag  # noqa: E402
from cold_frame.prompts.admission import AdmissionVerdict  # noqa: E402
from cold_frame.write.admission import ambiguous_spans  # noqa: E402

# 32 chars, verified entropy 4.25 → in the [4.0, 4.5) ambiguous band (>=32 for the _TOKEN scanner;
# not a definite secret, not a plain hash/uuid)
_AMBIG = "abcdefghijklmnopqrstabcdefghijkl"
_AMBIG_TEXT = f"my deploy id is {_AMBIG} ok"


def test_ambiguous_spans_detects_the_band() -> None:
    assert ambiguous_spans(_AMBIG_TEXT)  # the token is in [4.0, 4.5)
    assert scan_secret(_AMBIG_TEXT) is None  # ...but NOT a definite secret
    assert ambiguous_spans("just a short normal sentence about coffee") == []


def _tiebreak_mem(db_path, clock, *, verdict, is_local):  # type: ignore[no-untyped-def]
    from cold_frame.api import Memory

    from tests.conftest import ScriptedLLM

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
    from cold_frame.api import Memory

    from tests.conftest import ScriptedLLM

    llm = ScriptedLLM({}, is_local=False)  # empty script → any complete() call would AssertionError
    m = Memory(db_path, embedder=HashEmbedder(), llm=llm, clock=frozen_clock)
    res = m.add(_AMBIG_TEXT, raw=True)
    assert not res.added and res.blocked  # fail closed
    assert TaskTag.ADMISSION_TIEBREAK not in llm.calls  # the span never reached the remote model
