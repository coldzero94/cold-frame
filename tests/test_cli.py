"""CLI tests (P1 unit 9): offline add → search recall, doctor, mcp dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cold_frame.cli import main


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("COLD_FRAME_DB", str(db))
    return db


def test_cli_add_then_search_recall(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["add", "I prefer dark roast coffee"]) == 0
    assert "dark roast" in capsys.readouterr().out
    assert main(["search", "coffee roast"]) == 0
    assert "dark roast" in capsys.readouterr().out


def test_cli_search_no_match_is_success(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "I prefer dark roast coffee"])
    capsys.readouterr()
    assert main(["search", "zzz nothing qqq"]) == 0
    assert "no matches" in capsys.readouterr().out.lower()


def test_cli_doctor_reports_invariant(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "I prefer dark roast"])
    capsys.readouterr()
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out.lower()
    assert "integrity" in out
    assert "notes=1" in out and "fts=1" in out and "vec=1" in out


def test_cli_mcp_subcommand_dispatches(cli_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_mcp_main() -> int:
        calls["n"] += 1
        return 0

    monkeypatch.setattr("cold_frame.mcp.main", fake_mcp_main)
    assert main(["mcp"]) == 0
    assert calls["n"] == 1


def test_cli_add_without_text_errors(cli_db: Path) -> None:
    assert main(["add"]) == 1


def test_cli_worker_once_drains(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "a fact"])
    capsys.readouterr()
    assert main(["worker", "--once"]) == 0  # one drain pass, exits (no hang)
    assert "worker: ran" in capsys.readouterr().out


def test_cli_consolidate_dispatches(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "a fact"])
    capsys.readouterr()
    assert main(["consolidate"]) == 0
    assert "consolidate:" in capsys.readouterr().out


def test_cli_reembed_noop_with_default_embedder(
    cli_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["add", "I prefer dark roast coffee"])
    capsys.readouterr()
    assert main(["reembed"]) == 0  # CLI uses the default HashEmbedder → nothing stale
    assert "nothing stale" in capsys.readouterr().out


def test_cli_list_shows_active(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "I prefer dark roast coffee"])
    capsys.readouterr()
    assert main(["list"]) == 0
    assert "dark roast" in capsys.readouterr().out


def test_cli_show_by_id(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "I drive a Ferrari"])
    out = capsys.readouterr().out
    nid = out.split()[1]  # "+ <id>  <content>"
    assert main(["show", nid]) == 0
    shown = capsys.readouterr().out
    assert "Ferrari" in shown and "content:" in shown
    assert main(["show", "nope-ghost-id"]) == 1  # unknown id → exit 1


def test_cli_stats(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["add", "a fact one"])
    capsys.readouterr()
    assert main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "active=" in out and "by type:" in out


def test_cli_export_import_roundtrip(
    cli_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["add", "I prefer dark roast coffee"])
    capsys.readouterr()
    snap = tmp_path / "backup.db"
    assert main(["export", str(snap)]) == 0
    assert snap.exists() and "snapshot" in capsys.readouterr().out

    # mutate the live DB, then restore the snapshot → the mutation is gone, the original is back
    main(["add", "I also drive a Ferrari"])
    capsys.readouterr()
    assert main(["import", str(snap)]) == 0
    capsys.readouterr()
    assert main(["search", "Ferrari"]) == 0
    assert "no matches" in capsys.readouterr().out.lower()  # the post-snapshot add is gone
    assert main(["search", "coffee"]) == 0
    assert "dark roast" in capsys.readouterr().out  # the snapshot content is restored


def test_cli_setup_prints_wiring(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "claude mcp add" in out and "cold-frame mcp" in out  # the Claude Code wiring step
    assert "offline" in out  # reassures: no key, no network


def test_cli_timeline_shows_versions(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cold_frame.api import Memory

    m = Memory(str(cli_db))
    old = m.create_fact("I work at Vessl").added[0].id
    m.update_fact(old, "I work at Anthropic")  # supersede → old archived (v2 snapshot)
    m.close()
    assert main(["timeline", old]) == 0
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out and "archived" in out  # the belief trail
    assert main(["timeline", "ghost-id"]) == 1
    assert main(["timeline"]) == 1  # missing id


def test_cli_path_finds_and_misses(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cold_frame.api import Memory

    m = Memory(str(cli_db))
    old = m.create_fact("I work at Vessl").added[0].id
    new = m.update_fact(old, "I work at Anthropic").new.id  # supersedes edge new→old
    lone = m.create_fact("unrelated island fact").added[0].id
    m.close()

    assert main(["path", new, old]) == 0
    assert "supersedes" in capsys.readouterr().out  # the edge is on the path

    assert main(["path", new, lone]) == 1  # no edge connects them
    assert "no path" in capsys.readouterr().out

    assert main(["path", new, new]) == 0  # same note
    assert "same note" in capsys.readouterr().out

    assert main(["path", "ghost", lone]) == 1  # unknown src


def test_cli_path_multi_hop_and_max_hops(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    from cold_frame.api import Memory
    from cold_frame.models import Edge

    m = Memory(str(cli_db))
    a = m.create_fact("node A").added[0].id
    b = m.create_fact("node B").added[0].id
    c = m.create_fact("node C").added[0].id
    t = datetime(2026, 1, 1, tzinfo=UTC)
    m._store.add_edge(Edge(src_id=a, dst_id=b, relation="relates_to", created_at=t))
    m._store.add_edge(Edge(src_id=b, dst_id=c, relation="relates_to", created_at=t))
    m.close()

    # 2-hop chain A→B→C is found and prints BOTH edge steps (exercises the frontier expansion)
    assert main(["path", a, c]) == 0
    out = capsys.readouterr().out
    assert out.count("relates_to") == 2 and b[:8] in out

    # --max-hops boundary: reachable at 2 hops, NOT at 1 (guards the range(max_hops) bound)
    assert main(["path", a, c, "--max-hops", "2"]) == 0
    capsys.readouterr()
    assert main(["path", a, c, "--max-hops", "1"]) == 1
    assert "no path" in capsys.readouterr().out

    # reverse-direction traversal renders the "<-" arrow (edge is a→b, we walk b→a)
    assert main(["path", b, a]) == 0
    assert "<-[relates_to]" in capsys.readouterr().out


def test_cli_purge_requires_force(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cold_frame.api import Memory

    m = Memory(str(cli_db))
    fid = m.create_fact("unique-purgeable-phrase-xyz-77").added[0].id
    m.close()

    assert main(["purge", fid]) == 1  # destructive: a bare invocation refuses
    assert "--force" in capsys.readouterr().out
    assert main(["show", fid]) == 0  # still there
    capsys.readouterr()

    assert main(["purge", fid, "--force"]) == 0
    assert "scrubbed" in capsys.readouterr().out
    assert main(["show", fid]) == 1  # now permanently gone (not revivable)


def test_cli_show_ambiguous_prefix(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cold_frame.api import Memory
    from cold_frame.llm.base import HashEmbedder
    from cold_frame.models import Note, Scope, Source

    m = Memory(str(cli_db))
    emb = HashEmbedder()
    # two notes whose ids share an 8-char prefix → a short prefix can't disambiguate
    for suffix in ("aaaa", "bbbb"):
        nid = "deadbeef-0000-0000-0000-00000000" + suffix
        note = Note(
            id=nid,
            content=f"fact {suffix}",
            memory_type="semantic",
            scope=Scope(),
            created_at=m._clock.now(),
            valid_at=m._clock.now(),
            sources=[Source(kind="message", ref="m", content_hash="h", observed_at=m._clock.now())],
        )
        m._store.add_note(note, emb.embed_one(note.content))
    m.close()

    assert main(["show", "deadbeef"]) == 1  # ambiguous prefix → refuse, don't act on the wrong one
    assert "ambiguous" in capsys.readouterr().out.lower()
    # the full id still resolves unambiguously
    assert main(["show", "deadbeef-0000-0000-0000-00000000aaaa"]) == 0
    assert "fact aaaa" in capsys.readouterr().out


def test_cli_export_events_ndjson(
    cli_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["add", "a fact for the log"])
    capsys.readouterr()
    out = tmp_path / "events.ndjson"
    assert main(["export", str(out), "--events"]) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines and lines[0]["op"] == "create" and "event_id" in lines[0]
