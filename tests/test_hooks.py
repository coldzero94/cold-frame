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
    hooks = json.loads(sp.read_text())["hooks"]
    assert any(
        "hook session-start" in h["command"] for e in hooks["SessionStart"] for h in e["hooks"]
    )
    assert any(
        "hook stop" in h["command"] for e in hooks["Stop"] for h in e["hooks"]
    )  # capture too
    assert main(["--db", db, "hook", "install", "--project"]) == 0  # again → no duplicate
    after = json.loads(sp.read_text())["hooks"]
    assert len(after["SessionStart"]) == 1 and len(after["Stop"]) == 1


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


# ── P1 auto-capture (D26): transcript → Layer-A filter → enqueue → drain → WriteCore ──
import io  # noqa: E402

from cold_frame.integrations.claude_code import read_user_messages  # noqa: E402


def _write_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Write a Claude Code-shaped transcript .jsonl. turns = [(type, text)]; type ∈ user/assistant/
    tool_result (tool_result arrives under role=user with a tool_result content block)."""
    lines = []
    for typ, text in turns:
        if typ == "tool_result":
            msg = {"role": "user", "content": [{"type": "tool_result", "text": text}]}
            lines.append(json.dumps({"type": "user", "message": msg}))
        else:
            msg = {"role": typ, "content": [{"type": "text", "text": text}]}
            lines.append(json.dumps({"type": typ, "message": msg}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_layer_a_filter_keeps_only_user_text(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t,
        [
            ("user", "I deploy with ship.sh now"),
            ("assistant", "Got it, I will remember that going forward"),
            ("tool_result", "exit code 0, build succeeded"),
            ("user", "ok"),  # below _MIN_CHARS
            ("user", "My database is Postgres 16 in production"),
        ],
    )
    msgs, watermark = read_user_messages(t, 0)
    texts = [m["content"] for m in msgs]
    assert texts == ["I deploy with ship.sh now", "My database is Postgres 16 in production"]
    assert watermark == 5  # all lines consumed → next read starts after them


def test_capture_extracts_user_facts_through_writecore(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t,
        [
            ("user", "I deploy with ship.sh now"),
            ("assistant", "Understood, noting that for later"),
            ("user", "My database is Postgres 16 in production"),
        ],
    )
    mem = Memory(str(tmp_path / "m.db"))  # llm=None → naive on the Layer-A-filtered user msgs
    mem.enqueue_capture(str(t), "sess1")
    mem.run_pending_jobs()
    contents = [n.content for n in mem.list_active()]
    assert any("ship.sh" in c for c in contents)
    assert any("Postgres" in c for c in contents)
    assert not any("noting that for later" in c for c in contents)  # assistant turn dropped
    assert int(mem._store.get_meta("hook:watermark:sess1") or "0") == 3
    mem.close()


def test_capture_is_idempotent(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I prefer dark roast coffee always")])
    mem = Memory(str(tmp_path / "m.db"))
    mem.enqueue_capture(str(t), "s"), mem.run_pending_jobs()
    n1 = len(mem.list_active())
    mem.enqueue_capture(str(t), "s"), mem.run_pending_jobs()  # re-drain same span
    assert len(mem.list_active()) == n1  # watermark + dedup → no duplicate
    mem.close()


def test_hook_stop_enqueues_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I use vim with tabs everywhere")])
    db = str(tmp_path / "m.db")
    Memory(db).close()
    payload = json.dumps({"transcript_path": str(t), "session_id": "sess1"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main(["--db", db, "hook", "stop"]) == 0  # hook only enqueues (no drain)
    mem = Memory(db)
    assert not mem.list_active()  # nothing captured yet — pending
    mem.run_pending_jobs()  # the drain (a worker / MCP server) does the extraction
    assert any("vim" in n.content for n in mem.list_active())
    mem.close()


def test_mcp_search_drains_pending_captures(tmp_path: Path) -> None:
    # P2 loop-closer: a live MCP tool call drains pending capture jobs (in prod the host model
    # extracts; here llm=None → naive — the point is the DRAIN WIRING fires inside the tool).
    from cold_frame.mcp import _search_impl

    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I deploy with ship.sh in production now")])
    mem = Memory(str(tmp_path / "m.db"))
    mem.enqueue_capture(str(t), "s")
    assert not mem.list_active()  # enqueued, not yet drained
    _search_impl(mem, "anything")  # the tool call piggybacks the capture drain
    assert any("ship.sh" in n.content for n in mem.list_active())
    mem.close()


def test_layer_b_novelty_drops_known_keeps_new(tmp_path: Path) -> None:
    # P3a: a turn near-identical to an active note is dropped pre-extraction; a new one survives.
    mem = Memory(str(tmp_path / "m.db"))
    mem.add("I deploy with ship.sh in production")
    known = {"role": "user", "content": "I deploy with ship.sh in production"}
    fresh = {"role": "user", "content": "My cat is named Mocha"}
    filtered = mem._novel_messages([known, fresh], mem._default_scope)
    assert filtered == [fresh]  # the restatement is skipped, the new fact survives
    mem.close()


# ── dogfooding fixes (D26): compaction-loss, durability gate, max-len ──
def test_capture_survives_transcript_compaction(tmp_path: Path) -> None:
    # CRITICAL: a compacted (shortened) transcript must not freeze the watermark above EOF and lose
    # every later fact. After a shrink, re-scan from 0 (dedup collapses carry-over).
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I deploy with ship.sh now"), ("user", "I use Postgres 16")])
    mem = Memory(str(tmp_path / "m.db"))
    mem.enqueue_capture(str(t), "sess1")
    mem.run_pending_jobs()
    n_before = len(mem.list_active())
    # Claude Code compacts: the transcript is rewritten SHORTER, with a brand-new fact
    _write_transcript(t, [("user", "I switched the cache to Redis")])
    mem.enqueue_capture(str(t), "sess1")
    mem.run_pending_jobs()
    assert any("Redis" in n.content for n in mem.list_active())  # NOT lost after compaction
    assert len(mem.list_active()) >= n_before
    mem.close()


def test_layer_a_drops_task_requests_and_questions(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t,
        [
            ("user", "I deploy with ship.sh now"),  # durable fact → keep
            ("user", "can you run the tests again and show the output"),  # task-request → drop
            ("user", "show me the diff before you commit anything"),  # imperative → drop
            ("user", "how do I configure the linter here"),  # question → drop
            ("user", "My database is Postgres 16 in production"),  # durable fact → keep
        ],
    )
    texts = [m["content"] for m in read_user_messages(t, 0)[0]]
    assert texts == ["I deploy with ship.sh now", "My database is Postgres 16 in production"]


def test_layer_a_drops_oversized_paste(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "x" * 50_000)])  # a pasted blob, not a stated fact
    assert read_user_messages(t, 0)[0] == []


def test_readd_reinforces_existing_note(tmp_path: Path) -> None:
    # dogfood fix: a restatement via the WriteCore path reinforces the survivor (was a no-op).
    mem = Memory(str(tmp_path / "m.db"))
    fid = mem.add("I deploy with ship.sh in production").added[0].id
    a0 = mem.get(fid).access_count
    mem.add("I deploy with ship.sh in production")  # exact restate → dedup → reinforce
    assert mem.get(fid).access_count > a0
    mem.close()


def test_capture_restatement_reinforces_via_layer_b(tmp_path: Path) -> None:
    # dogfood fix: Layer-B drops the restatement but reinforces the matched note (repeats count).
    mem = Memory(str(tmp_path / "m.db"))
    fid = mem.add("I prefer dark roast coffee always").added[0].id
    a0 = mem.get(fid).access_count
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I prefer dark roast coffee always")])
    mem.enqueue_capture(str(t), "s")
    mem.run_pending_jobs()
    assert mem.get(fid).access_count > a0  # the repeat bumped the existing note
    mem.close()


def test_doctor_flags_dead_jobs_as_problem(tmp_path: Path) -> None:
    # dogfood fix: doctor must not stay green while capture jobs die. dead jobs → PROBLEMS (exit 1).
    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem._store.enqueue("capture", {"x": 1}, dedup_key="k")
    mem._store._conn.execute("UPDATE jobs SET status='dead'")
    mem._store._conn.commit()
    assert mem._store.dead_count() == 1 and mem._store.pending_count("capture") == 0
    mem.close()
    assert main(["--db", db, "doctor"]) == 1  # not "ok"


def test_hook_install_handles_unwritable_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a read-only / managed .claude must yield a clean exit 1, not a PermissionError traceback.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").write_text("not a directory")  # mkdir(.claude) will fail
    db = str(tmp_path / "m.db")
    Memory(db).close()
    assert main(["--db", db, "hook", "install", "--project"]) == 1
