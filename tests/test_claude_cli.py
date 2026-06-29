"""ClaudeCliLLM (D26): borrow the user's Claude session via headless `claude -p` (no API key)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from cold_frame.llm.base import TaskTag
from cold_frame.llm.claude_cli import ClaudeCliLLM
from cold_frame.prompts.scope import SCOPE_SYSTEM, ScopeVerdict, build_scope_user


def _fake_run(stdout: str, code: int = 0):  # type: ignore[no-untyped-def]
    return lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr="")


def test_claude_cli_parses_envelope_and_validates_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    # the `claude -p --output-format json` envelope: .result holds the model's JSON text
    envelope = '{"type":"result","result":"{\\"tiers\\":[\\"global\\",\\"project\\"]}"}'
    monkeypatch.setattr(subprocess, "run", _fake_run(envelope))
    res = ClaudeCliLLM().complete(
        task=TaskTag.SCOPE_CLASSIFY, system="s", user="u", schema=ScopeVerdict
    )
    assert isinstance(res.parsed, ScopeVerdict)
    assert res.parsed.tiers == ["global", "project"]


def test_claude_cli_tolerates_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = '{"result":"```json\\n{\\"tiers\\":[\\"project\\"]}\\n```"}'
    monkeypatch.setattr(subprocess, "run", _fake_run(envelope))
    res = ClaudeCliLLM().complete(
        task=TaskTag.SCOPE_CLASSIFY, system="s", user="u", schema=ScopeVerdict
    )
    assert isinstance(res.parsed, ScopeVerdict) and res.parsed.tiers == ["project"]


def test_claude_cli_degrades_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # nonzero exit, garbage output, missing binary all → parsed=None (engine goes deterministic)
    monkeypatch.setattr(subprocess, "run", _fake_run("", code=1))
    assert (
        ClaudeCliLLM()
        .complete(task=TaskTag.EXTRACT, system="s", user="u", schema=ScopeVerdict)
        .parsed
        is None
    )
    monkeypatch.setattr(subprocess, "run", _fake_run("not json at all"))
    assert (
        ClaudeCliLLM()
        .complete(task=TaskTag.EXTRACT, system="s", user="u", schema=ScopeVerdict)
        .parsed
        is None
    )

    def _boom(*a: object, **k: object) -> object:
        raise OSError("claude not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert (
        ClaudeCliLLM()
        .complete(task=TaskTag.EXTRACT, system="s", user="u", schema=ScopeVerdict)
        .parsed
        is None
    )


@pytest.mark.live
def test_claude_cli_real_session_extraction() -> None:
    # uses the REAL `claude` CLI + the user's session (no API key). Opt-in (costs a call): run with
    # COLD_FRAME_LIVE=1 so it never fires — or bills — in the normal gate.
    import os

    if not os.environ.get("COLD_FRAME_LIVE"):
        pytest.skip("set COLD_FRAME_LIVE=1 to run the live claude-CLI test")
    if not ClaudeCliLLM.available():
        pytest.skip("claude CLI not on PATH")
    res = ClaudeCliLLM().complete(
        task=TaskTag.SCOPE_CLASSIFY,
        system=SCOPE_SYSTEM,
        user=build_scope_user(["I prefer dark roast coffee", "this repo deploys with ship.sh"]),
        schema=ScopeVerdict,
    )
    assert isinstance(res.parsed, ScopeVerdict)
    assert res.parsed.tiers == ["global", "project"]  # the real model classifies correctly


def test_claude_subprocess_env_excludes_the_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # the at-rest encryption key must NOT leak into the third-party `claude` child env (I16 / trust
    # boundary) — a same-user reader of /proc/<pid>/environ must not recover it.
    monkeypatch.setenv("COLD_FRAME_KEY", "super-secret-master-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")  # would make capture metered
    monkeypatch.setenv("COLD_FRAME_EXTRACTING", "stale")  # the forced guard must override this
    monkeypatch.setenv("COLD_FRAME_DB", "/tmp/x.db")  # a path, not a secret — may be inherited
    captured: dict[str, str] = {}

    def _spy(*a: object, **k: object) -> object:
        captured.update(k.get("env") or {})  # type: ignore[arg-type]
        return SimpleNamespace(returncode=0, stdout='{"type":"result","result":"{}"}', stderr="")

    monkeypatch.setattr(subprocess, "run", _spy)
    ClaudeCliLLM().complete(task=TaskTag.EXTRACT, system="s", user="u")
    assert "COLD_FRAME_KEY" not in captured  # scrubbed (secret)
    assert "ANTHROPIC_API_KEY" not in captured  # scrubbed → session auth, not metered (D26)
    assert captured.get("COLD_FRAME_EXTRACTING") == "1"  # the guard wins over the inherited value
