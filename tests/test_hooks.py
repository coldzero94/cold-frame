"""P0 auto-recall (D26): the SessionStart hook + install/status, via the `cold-frame hook` CLI.

Read-only recall — no capture/write path here (that's P1+). The hook must inject the strongest
durable memories as additionalContext, stay silent when there's nothing, and never crash a session.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.cli import main


def _seed(db: str) -> None:
    m = Memory(db)
    for t in ("I prefer dark roast coffee", "I deploy with ship.sh", "I use vim with tabs"):
        m.add(t)
    m.close()


def test_session_start_emits_recall_context(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "m.db")
    _seed(db)
    assert main(["--db", db, "hook", "session-start"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "Coldframe" in ctx and "dark roast" in ctx  # the user's belief is surfaced


def test_session_start_empty_memory_emits_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "m.db")
    Memory(db).close()  # empty DB → silence > noise
    assert main(["--db", db, "hook", "session-start"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_session_start_never_crashes_on_bad_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "not.db"
    bad.write_text("not a database")  # a hook must degrade to a silent no-op, never raise
    assert main(["--db", str(bad), "hook", "session-start"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_hook_install_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "m.db")
    Memory(db).close()
    assert main(["--db", db, "hook", "install", "--project"]) == 0
    sp = tmp_path / ".claude" / "settings.json"
    entries = json.loads(sp.read_text())["hooks"]["SessionStart"]
    assert any("hook session-start" in h["command"] for e in entries for h in e["hooks"])
    assert main(["--db", db, "hook", "install", "--project"]) == 0  # again → no duplicate
    assert len(json.loads(sp.read_text())["hooks"]["SessionStart"]) == 1


def test_hook_install_preserves_existing_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / ".claude" / "settings.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": []}]}}))
    db = str(tmp_path / "m.db")
    Memory(db).close()
    assert main(["--db", db, "hook", "install", "--project"]) == 0
    s = json.loads(sp.read_text())
    assert s["model"] == "opus"  # untouched
    assert "Stop" in s["hooks"] and "SessionStart" in s["hooks"]  # merged, not clobbered
