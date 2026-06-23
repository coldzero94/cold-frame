"""SamplingLLM tests — the host-sampling seam (ride on Claude Code's model, degrade-safe)."""

from __future__ import annotations

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import PolicyError
from cold_frame.llm.base import HashEmbedder, TaskTag
from cold_frame.llm.sampling import SamplingLLM
from cold_frame.models import ConflictVerdict

from tests.conftest import FrozenClock


def _llm(reply: str) -> SamplingLLM:
    return SamplingLLM(lambda system, user: reply)


def test_parses_json_into_schema() -> None:
    res = _llm('{"relation": "duplicate", "confidence": 0.9, "rationale": "same"}').complete(
        task=TaskTag.DEDUP_BATCH, system="s", user="u", schema=ConflictVerdict
    )
    assert isinstance(res.parsed, ConflictVerdict) and res.parsed.relation == "duplicate"


def test_parses_json_inside_markdown_fence() -> None:
    reply = 'Sure!\n```json\n{"relation": "contradiction", "confidence": 0.8}\n```\n'
    res = _llm(reply).complete(
        task=TaskTag.CONFLICT_JUDGE, system="s", user="u", schema=ConflictVerdict
    )
    assert isinstance(res.parsed, ConflictVerdict) and res.parsed.relation == "contradiction"


def test_garbage_reply_degrades_to_none() -> None:
    res = _llm("I cannot help with that.").complete(
        task=TaskTag.DEDUP_BATCH, system="s", user="u", schema=ConflictVerdict
    )
    assert res.parsed is None  # → deterministic engine decides (offline behavior)


def test_sampler_exception_degrades_not_raises() -> None:
    def _boom(system: str, user: str) -> str:
        raise RuntimeError("host does not support sampling")

    res = SamplingLLM(_boom).complete(
        task=TaskTag.DEDUP_BATCH, system="s", user="u", schema=ConflictVerdict
    )
    assert res.parsed is None and res.text == ""  # safe degrade, no exception escapes


def test_empty_reply_degrades() -> None:
    res = _llm("").complete(task=TaskTag.DEDUP_BATCH, system="s", user="u", schema=ConflictVerdict)
    assert res.parsed is None


def test_remote_host_blocked_from_secret_tiebreak() -> None:
    llm = SamplingLLM(lambda s, u: "{}")  # is_local defaults False (host = remote)
    assert llm.is_local is False
    with pytest.raises(PolicyError):  # I7: a secret/PII tiebreak must never ride on a remote host
        llm.assert_local_for(TaskTag.ADMISSION_TIEBREAK)
    llm.assert_local_for(TaskTag.DEDUP_BATCH)  # non-secret tasks are fine


# ── end-to-end through Memory: host sampling drives dedup; failure → offline ──
def test_memory_with_host_sampling_dedups(db_path: str, frozen_clock: FrozenClock) -> None:
    dup = SamplingLLM(lambda s, u: '{"relation": "duplicate", "confidence": 0.9}')
    m = Memory(db_path, embedder=HashEmbedder(), llm=dup, clock=frozen_clock)
    m.add("dark roast coffee", raw=True)
    res = m.add("dark roast coffee beans", raw=True)  # 0.866 band → host judges duplicate → merged
    assert res.deduped and not res.added
    assert len(m.list_active()) == 1


def test_memory_degrades_to_offline_when_sampling_unavailable(
    db_path: str, frozen_clock: FrozenClock
) -> None:
    def _unavailable(system: str, user: str) -> str:
        raise RuntimeError("no sampling capability")

    m = Memory(db_path, embedder=HashEmbedder(), llm=SamplingLLM(_unavailable), clock=frozen_clock)
    m.add("dark roast coffee", raw=True)
    res = m.add("dark roast coffee beans", raw=True)  # judge unavailable → kept distinct (offline)
    assert res.added and not res.deduped
    assert len(m.list_active()) == 2
