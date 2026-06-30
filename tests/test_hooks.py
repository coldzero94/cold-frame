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
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.models import Scope

    m = Memory(db)
    for t in ("I prefer dark roast coffee", "I deploy with ship.sh", "I use vim with tabs"):
        m.add(t, scope=Scope(agent_id=GLOBAL_KEY))  # global tier → recalled in every session
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
    assert any(
        "hook user-prompt" in h["command"] for e in hooks["UserPromptSubmit"] for h in e["hooks"]
    )  # incremental recall too
    assert main(["--db", db, "hook", "install", "--project"]) == 0  # again → no duplicate
    after = json.loads(sp.read_text())["hooks"]
    assert len(after["SessionStart"]) == 1 and len(after["Stop"]) == 1
    assert len(after["UserPromptSubmit"]) == 1


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
    assert main(["--db", db, "hook", "stop"]) == 0  # B6: the Stop hook enqueues AND naive-drains
    mem = Memory(db)
    assert any("vim" in n.content for n in mem.list_active())  # captured at turn-end, no worker run
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
    novel, known_ids = mem._novel_messages([known, fresh], mem._default_scope)
    assert novel == [fresh]  # the restatement is skipped, the new fact survives
    assert len(known_ids) == 1  # the matched note id is returned for the caller to reinforce
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
    # "I prefer…" routes to the GLOBAL tier, so seed it there (dedup/reinforce is per-tier, D26).
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.models import Scope

    mem = Memory(str(tmp_path / "m.db"))
    fid = mem.add("I prefer dark roast coffee always", scope=Scope(agent_id=GLOBAL_KEY)).added[0].id
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


def test_auto_capture_does_not_bloat_on_repetition(tmp_path: Path) -> None:
    # regression baseline (dogfood POSITIVE): re-stating the same facts across sessions must NOT
    # grow the active set — the anti-bloat claim. Attack bloat at admission, not the growth curve.
    facts = [
        "I prefer dark roast coffee",
        "I deploy with ship.sh always",
        "My database is Postgres",
    ]
    mem = Memory(str(tmp_path / "m.db"))
    t1 = tmp_path / "s1.jsonl"
    _write_transcript(t1, [("user", f) for f in facts])
    mem.enqueue_capture(str(t1), "s1")
    mem.run_pending_jobs()
    n1 = len(mem.list_active())
    assert n1 == 3
    for i in (2, 3, 4):  # later sessions re-state the SAME facts → flat (dedup + Layer-B collapse)
        ti = tmp_path / f"s{i}.jsonl"
        _write_transcript(ti, [("user", f) for f in facts])
        mem.enqueue_capture(str(ti), f"s{i}")
        mem.run_pending_jobs()
    assert len(mem.list_active()) == n1  # no turn-proportional bloat
    mem.close()


# ── project scoping (D26): git-based tag + global tier ──
def test_project_key_is_git_remote_based(tmp_path: Path) -> None:
    from cold_frame.integrations.claude_code import project_key

    def mkrepo(p: Path, remote: str) -> Path:
        (p / ".git").mkdir(parents=True)
        (p / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {remote}\n')
        return p

    a = mkrepo(tmp_path / "clone-a", "git@github.com:me/app.git")
    b = mkrepo(tmp_path / "clone-b", "git@github.com:me/app.git")  # same repo, different path
    c = mkrepo(tmp_path / "other", "git@github.com:me/other.git")
    assert project_key(str(a)) == project_key(str(b))  # path-independent (remote-based)
    assert project_key(str(a)) != project_key(str(c))  # different repo → different tag


def test_project_key_shares_across_git_worktree(tmp_path: Path) -> None:
    # a linked worktree has .git as a FILE ('gitdir: <path>'); it must resolve to the SHARED repo's
    # remote (via commondir) so the worktree and the main checkout get ONE project_key, not two.
    from cold_frame.integrations.claude_code import project_key

    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / ".git" / "config").write_text('[remote "origin"]\n\turl = git@github.com:me/app.git\n')
    wt_gitdir = main / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")  # → main/.git (holds the remote)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")  # the worktree's .git is a FILE pointer

    assert project_key(str(wt)) == project_key(str(main))  # shared remote → same tag
    assert project_key(str(wt)).startswith("proj:")  # a real project tag, not the cwd fallback


def test_auto_capture_scopes_by_project_with_global_tier(tmp_path: Path) -> None:
    from cold_frame.integrations.claude_code import GLOBAL_KEY, project_key
    from cold_frame.models import Scope

    mem = Memory(str(tmp_path / "m.db"))
    a_dir = tmp_path / "repoA"
    a_dir.mkdir()
    ta = tmp_path / "a.jsonl"
    _write_transcript(
        ta,
        [
            ("user", "this project uses pnpm not npm"),  # project fact → repoA tier
            ("user", "I prefer dark roast coffee"),  # personal → global tier
        ],
    )
    mem.enqueue_capture(str(ta), "sa", str(a_dir))
    mem.run_pending_jobs()
    key_a, key_b = project_key(str(a_dir)), project_key(str(tmp_path / "repoB"))
    a_facts = [n.content for n in mem.list_active(scope=Scope(agent_id=key_a))]
    g_facts = [n.content for n in mem.list_active(scope=Scope(agent_id=GLOBAL_KEY))]
    b_facts = [n.content for n in mem.list_active(scope=Scope(agent_id=key_b))]
    assert any("pnpm" in c for c in a_facts)  # project fact lives in repoA
    assert any("dark roast" in c for c in g_facts)  # personal fact is global
    assert not any("pnpm" in c for c in g_facts)  # project fact did NOT leak to global
    assert not any("pnpm" in c for c in b_facts)  # …nor to another project (isolation)
    mem.close()


def test_classify_tiers_uses_the_llm_when_present(tmp_path: Path) -> None:
    # the parasitic host model (or a local one) classifies tier; a ScriptedLLM stands in here.
    from cold_frame.eval.harness import LlmScriptEntry, ScriptedLLM
    from cold_frame.llm.base import TaskTag

    script = [
        LlmScriptEntry(
            task=TaskTag.SCOPE_CLASSIFY,
            match={"any": True},
            returns={"tiers": ["global", "project"]},
        )
    ]
    mem = Memory(str(tmp_path / "m.db"), llm=ScriptedLLM(script))
    assert mem._classify_tiers(["I work at Acme", "this repo uses pnpm"]) == [True, False]
    mem.close()


def test_classify_tiers_falls_back_to_heuristic_offline(tmp_path: Path) -> None:
    mem = Memory(str(tmp_path / "m.db"))  # llm=None → deterministic heuristic
    assert mem._classify_tiers(["I prefer dark roast", "this repo uses pnpm"]) == [True, False]
    mem.close()


# ── PR-review fixes: MCP scope isolation, recall scoping, doctor stale-backlog, fallbacks ──
def test_mcp_search_does_not_leak_across_projects(tmp_path: Path) -> None:
    # C1 regression: the MCP tool path must scope to its project + global, never all tiers.
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.mcp import _search_impl
    from cold_frame.models import Scope

    mem = Memory(str(tmp_path / "m.db"), default_scope=Scope(agent_id="proj:A"))
    mem.add("apple alpha aardvark", scope=Scope(agent_id="proj:A"))
    mem.add("banana bravo beetle", scope=Scope(agent_id="proj:B"))
    mem.add("cherry charlie cobra", scope=Scope(agent_id=GLOBAL_KEY))
    contents = " ".join(h["content"] for h in _search_impl(mem, "apple banana cherry")["hits"])
    assert "apple" in contents  # this project
    assert "cherry" in contents  # global tier
    assert "banana" not in contents  # project B must NOT leak through the tool path
    mem.close()


def test_session_start_recall_is_project_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # confidentiality: recall in project A must not surface project B's facts.
    from cold_frame.integrations.claude_code import project_key
    from cold_frame.models import Scope

    dir_a, dir_b = tmp_path / "repoA", tmp_path / "repoB"
    dir_a.mkdir()
    dir_b.mkdir()
    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem.add("alpha apricot lives in repo A", scope=Scope(agent_id=project_key(str(dir_a))))
    mem.add("bravo banana lives in repo B", scope=Scope(agent_id=project_key(str(dir_b))))
    mem.close()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(dir_a)})))
    assert main(["--db", db, "hook", "session-start"]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "apricot" in ctx  # this project's fact
    assert "banana" not in ctx  # repo B's fact must not leak into repo A's recall


def test_end_to_end_capture_then_recall_same_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # the actual product loop: a Stop-hook capture resurfaces in a later session's recall.
    dir_a = tmp_path / "repoA"
    dir_a.mkdir()
    db = str(tmp_path / "m.db")
    Memory(db).close()
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [("user", "I deploy this repo with ship.sh nightly")])
    payload = {"transcript_path": str(t), "session_id": "s", "cwd": str(dir_a)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["--db", db, "hook", "stop"]) == 0  # enqueue
    mem = Memory(db)
    mem.run_pending_jobs()  # drain (a worker)
    mem.close()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(dir_a)})))
    assert main(["--db", db, "hook", "session-start"]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "ship.sh" in ctx  # captured in repo A → recalled in repo A


def test_doctor_flags_stale_pending_backlog(tmp_path: Path) -> None:
    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem._store.enqueue("capture", {"x": 1}, dedup_key="k")
    mem._store._conn.execute("UPDATE jobs SET created_at = ?", ("2020-01-01T00:00:00Z",))
    mem._store._conn.commit()
    assert (mem._store.oldest_pending_age(now=mem._clock.now()) or 0) > 86_400
    mem.close()
    assert main(["--db", db, "doctor"]) == 1  # stale backlog → PROBLEMS


def test_oldest_pending_age_none_when_empty(tmp_path: Path) -> None:
    mem = Memory(str(tmp_path / "m.db"))
    assert mem._store.oldest_pending_age(now=mem._clock.now()) is None
    mem.close()


def test_classify_tiers_falls_back_on_length_mismatch(tmp_path: Path) -> None:
    from cold_frame.eval.harness import LlmScriptEntry, ScriptedLLM
    from cold_frame.llm.base import TaskTag

    # LLM returns 1 label for 2 inputs → mismatch → safe degrade to the heuristic.
    script = [
        LlmScriptEntry(
            task=TaskTag.SCOPE_CLASSIFY, match={"any": True}, returns={"tiers": ["global"]}
        )
    ]
    mem = Memory(str(tmp_path / "m.db"), llm=ScriptedLLM(script))
    assert mem._classify_tiers(["I prefer dark roast", "this repo uses pnpm"]) == [True, False]
    mem.close()


def test_read_user_messages_never_raises_on_bad_encoding(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    good = json.dumps({"type": "user", "message": {"role": "user", "content": "I use Postgres 16"}})
    t.write_bytes(good.encode("utf-8") + b"\n\xff\xfe garbage bytes \x80\n")
    msgs, _ = read_user_messages(t, 0)  # must not raise on the non-UTF-8 line
    assert any("Postgres" in m["content"] for m in msgs)


def test_full_capture_loop_with_llm_routing_and_extraction(tmp_path: Path) -> None:
    # closes review gap G1/G4: the capture drain with a real extracting+classifying LLM, end-to-end,
    # routing two facts into two tiers. A host-model stand-in implements the real LLM seam.
    import re

    from cold_frame.integrations.claude_code import GLOBAL_KEY, project_key
    from cold_frame.llm.base import LLM, LLMResult, TaskTag
    from cold_frame.models import Scope
    from cold_frame.prompts.extract import ExtractedFact, ExtractionOutput
    from cold_frame.prompts.scope import ScopeVerdict

    class _LoopLLM(LLM):
        name = "loop"

        def __init__(self) -> None:
            self.used: set[str] = set()

        @property
        def is_local(self) -> bool:
            return False

        def complete(  # type: ignore[no-untyped-def]
            self, *, task, system, user, schema=None, temperature=0.0, max_tokens=1024
        ):
            self.used.add(task.value)
            if task == TaskTag.SCOPE_CLASSIFY:
                lines = [ln for ln in user.splitlines() if re.match(r"^\d+\.", ln)]
                stmts = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in lines]
                tiers = ["global" if s.lower().startswith("i ") else "project" for s in stmts]
                return LLMResult(parsed=ScopeVerdict(tiers=tiers), model=self.name)
            if task == TaskTag.EXTRACT:
                m = re.search(r"## New Messages\n(.*?)\n\n## Observation", user, re.DOTALL)
                msgs = json.loads(m.group(1)) if m else []
                facts = [
                    ExtractedFact(
                        text=x["content"],
                        memory_type="semantic",
                        confidence=0.85,
                        durability="durable",
                    )
                    for x in msgs
                ]
                return LLMResult(parsed=ExtractionOutput(facts=facts), model=self.name)
            return LLMResult(parsed=None, model=self.name)

    repo = tmp_path / "repo"
    repo.mkdir()
    llm = _LoopLLM()
    mem = Memory(str(tmp_path / "m.db"), llm=llm)
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t, [("user", "this repo uses pnpm not npm"), ("user", "I prefer dark roast coffee")]
    )
    mem.enqueue_capture(str(t), "s", str(repo))
    mem.run_pending_jobs()
    assert {"scope_classify", "extract"} <= llm.used  # the LLM drove routing AND extraction
    proj = [n.content for n in mem.list_active(scope=Scope(agent_id=project_key(str(repo))))]
    glob = [n.content for n in mem.list_active(scope=Scope(agent_id=GLOBAL_KEY))]
    assert any("pnpm" in c for c in proj) and not any("pnpm" in c for c in glob)  # project → repo
    assert any("dark roast" in c for c in glob)  # personal fact → global tier
    assert not any("dark roast" in c for c in proj)
    mem.close()


def test_capture_tier_failure_is_isolated_and_advances_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # H2 fix: if one tier's add() raises, the other tier still commits, the job does NOT raise, and
    # the watermark advances (no re-present → no double-reinforce on retry).
    from cold_frame.exceptions import StoreError
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.models import Scope

    mem = Memory(str(tmp_path / "m.db"))
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t, [("user", "I prefer dark roast coffee"), ("user", "this repo uses pnpm not npm")]
    )
    orig_add = mem.add

    def flaky_add(messages, **kw):  # type: ignore[no-untyped-def]
        scope = kw.get("scope")
        if scope is not None and scope.agent_id != GLOBAL_KEY:  # the project tier blows up
            raise StoreError("boom")
        return orig_add(messages, **kw)

    monkeypatch.setattr(mem, "add", flaky_add)
    mem.enqueue_capture(str(t), "s", "/work/repo")
    mem.run_pending_jobs()  # must NOT raise despite the project-tier failure
    glob = [n.content for n in mem.list_active(scope=Scope(agent_id=GLOBAL_KEY))]
    assert any("dark roast" in c for c in glob)  # the global tier committed
    assert int(mem._store.get_meta("hook:watermark:s") or "0") == 2  # advanced past both lines
    mem.close()


def test_hook_user_prompt_injects_relevant_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.models import Scope

    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem.add("I deploy this service with ship.sh to fly.io", scope=Scope(agent_id=GLOBAL_KEY))
    mem.add("I prefer dark roast coffee", scope=Scope(agent_id=GLOBAL_KEY))
    mem.close()
    # a prompt about deploying → the ship.sh memory is surfaced; coffee is not
    prompt = json.dumps({"prompt": "how should I deploy this"})
    monkeypatch.setattr("sys.stdin", io.StringIO(prompt))
    assert main(["--db", db, "hook", "user-prompt"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "ship.sh" in ctx


def test_hook_user_prompt_silent_on_unrelated_and_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cold_frame.integrations.claude_code import GLOBAL_KEY
    from cold_frame.models import Scope

    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem.add("I deploy with ship.sh to fly.io", scope=Scope(agent_id=GLOBAL_KEY))
    mem.close()
    unrelated = json.dumps({"prompt": "quantum chromodynamics lecture"})
    monkeypatch.setattr("sys.stdin", io.StringIO(unrelated))
    assert main(["--db", db, "hook", "user-prompt"]) == 0
    assert capsys.readouterr().out.strip() == ""  # no lexical overlap → no per-turn noise
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "hi"})))  # too short
    assert main(["--db", db, "hook", "user-prompt"]) == 0
    assert capsys.readouterr().out.strip() == ""


@pytest.mark.slow
def test_anti_bloat_at_scale(tmp_path: Path) -> None:
    # the core moat claim, stress-tested: thousands of captured turns from a BOUNDED universe of
    # durable facts (plus chatter) must keep the active set bounded by the universe, NOT the turn
    # count. Without dedup/Layer-B/caps this would grow to thousands; with them it converges.
    import random

    rng = random.Random(42)
    universe = [
        f"my preferred {tool} for {task} is option {tool[:3]}{task[:2]}"
        for tool in ("editor", "shell", "linter", "browser", "vcs", "db", "tracker", "ci")
        for task in ("dev", "review", "deploy", "debug", "test")
    ]  # 40 distinct durable facts
    chatter = ["run the tests again", "what does this do?", "show me the diff", "fix it", "ok"]
    mem = Memory(str(tmp_path / "m.db"))
    turns_total = 1200
    for sess, _start in enumerate(range(0, turns_total, 12)):
        batch = []
        for _ in range(12):
            r = rng.random()
            batch.append(rng.choice(chatter) if r < 0.35 else rng.choice(universe))
        t = tmp_path / f"s{sess}.jsonl"
        _write_transcript(t, [("user", x) for x in batch])
        mem.enqueue_capture(str(t), f"s{sess}", "/work/repo")
        mem.run_pending_jobs()
    active = len(mem.list_active(limit=100_000))
    print(f"\n  {turns_total} turns -> {active} active (universe={len(universe)})")
    assert active <= len(universe) + 5  # bounded by distinct facts, not turn count
    assert active < turns_total // 10  # clearly sublinear in turns
    mem.close()


def test_hook_install_wires_hooks_without_touching_claude_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the CLAUDE.md directive was removed (agent-push now ships in the plugin skill); `hook install`
    # is the non-plugin fallback that only wires settings.json hooks, never edits CLAUDE.md.
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "m.db")
    Memory(db).close()
    assert main(["--db", db, "hook", "install", "--project"]) == 0
    assert (tmp_path / ".claude" / "settings.json").exists()  # hooks wired
    assert not (tmp_path / "CLAUDE.md").exists()  # no per-machine CLAUDE.md edit
    assert main(["--db", db, "hook", "uninstall", "--project"]) == 0  # removable


def test_layer_a_drops_harness_and_slash_noise(tmp_path: Path) -> None:
    # dogfood fix: harness/slash-command/bash blocks arrive as type=user but are not user facts.
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t,
        [
            ("user", "<command-name>/effort</command-name> set to xhigh"),
            ("user", "<bash-input>code .</bash-input>"),
            ("user", "<task-notification> task done </task-notification>"),
            ("user", "<local-command-caveat>Caveat: generated by a command</local-command-caveat>"),
            ("user", "[Request interrupted by user]"),
            ("user", "My database is Postgres 16 in production"),  # the one real fact
        ],
    )
    msgs, _ = read_user_messages(t, 0)
    texts = [m["content"] for m in msgs]
    assert texts == ["My database is Postgres 16 in production"]  # only the real fact survives


def test_jobs_retry_dead_recovers_lost_captures(tmp_path: Path) -> None:
    # dead-letter recovery: a dead job is revived to pending (no silently-lost-forever captures).
    db = str(tmp_path / "m.db")
    mem = Memory(db)
    mem._store.enqueue("capture", {"x": 1}, dedup_key="k")
    mem._store._conn.execute("UPDATE jobs SET status='dead'")
    mem._store._conn.commit()
    assert mem._store.dead_count() == 1
    assert mem._store.requeue_dead(now=mem._clock.now()) == 1
    assert mem._store.dead_count() == 0 and mem._store.pending_count() == 1
    mem.close()
    assert main(["--db", db, "jobs", "--retry-dead"]) == 0  # CLI path works too


def test_cli_search_as_of_time_travel(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # the rewindable-belief differentiator, now exposed on the CLI: --as-of filters by bi-temporal
    # validity. A fact added now is NOT valid as-of a past date, but IS as-of a future one.
    db = str(tmp_path / "m.db")
    Memory(db).add("I deploy with ship.sh")  # valid_at ~ now
    assert main(["--db", db, "search", "deploy", "--as-of", "2020-01-01"]) == 0
    assert "ship.sh" not in capsys.readouterr().out  # didn't exist back then
    assert main(["--db", db, "search", "deploy", "--as-of", "2099-01-01"]) == 0
    assert "ship.sh" in capsys.readouterr().out  # valid as-of the future
    assert main(["--db", db, "search", "deploy", "--as-of", "not-a-date"]) == 1  # clean error


def test_capture_dedup_drops_rescan_of_archived_fact(memory: Memory) -> None:
    # a Claude Code compaction shrink forces a full transcript re-scan; re-reading a turn whose note
    # was since archived/superseded must NOT resurrect it (or flip a belief backward) — Layer-B now
    # dedups against archived too. A genuine revival flows through the agent-push add path.
    from cold_frame.models import Scope

    scope = Scope(agent_id="proj:demo")
    nid = memory.add("I deploy with ship.sh", scope=scope).added[0].id
    memory.forget(nid)  # archive (forgotten)
    fresh, known = memory._novel_messages(
        [{"role": "user", "content": "I deploy with ship.sh"}], scope
    )
    assert fresh == []  # the re-read is dropped — not re-added (no resurrection)
    assert known == []  # and not reinforced (archived, not a live restatement)

    # sanity: a near-identical match to a LIVE note is still 'known' (reinforced post-commit)
    live = memory.add("I use the dark theme everywhere", scope=scope).added[0].id
    f2, k2 = memory._novel_messages(
        [{"role": "user", "content": "I use the dark theme everywhere"}], scope
    )
    assert f2 == [] and k2 == [live]


def test_layer_a_keeps_homograph_declarative_facts() -> None:
    # noun/verb homographs lead real declarative facts; a copula/modal keeps them, while a bare
    # imperative starting with the same verb is still dropped.
    from cold_frame.integrations.claude_code import _is_durable_user_fact

    assert _is_durable_user_fact("test coverage must exceed 80% here")  # kept (declarative)
    assert _is_durable_user_fact("review is mandatory before any merge")  # kept
    assert not _is_durable_user_fact("run the integration tests again")  # imperative → dropped
    assert not _is_durable_user_fact("fix the failing build now please")  # imperative → dropped


def test_project_key_empty_cwd_is_isolated_not_global() -> None:
    from cold_frame.integrations.claude_code import GLOBAL_KEY, LOCAL_KEY, project_key

    assert project_key("") == LOCAL_KEY  # no cwd → isolated local bucket
    assert project_key(None) == LOCAL_KEY
    assert LOCAL_KEY != GLOBAL_KEY  # ...and NOT the cross-project global tier (D26 leak guard)


def test_read_user_messages_does_not_drop_a_partial_trailing_line(tmp_path: Path) -> None:
    # a mid-write last line (no trailing newline) must NOT be counted in the watermark, so the
    # completed turn is still picked up on the next drain (not permanently dropped).
    t = tmp_path / "t.jsonl"
    rec = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "I use ruff for linting"}],
        },
    }
    partial = '{"type": "user", "message": {"role": "user", "content": [{"type": "text", "te'
    t.write_text(json.dumps(rec) + "\n" + partial, encoding="utf-8")  # NO trailing newline
    msgs, watermark = read_user_messages(t, 0)
    assert [m["content"] for m in msgs] == ["I use ruff for linting"]
    assert watermark == 1  # only the complete record is counted; the partial isn't consumed/dropped
