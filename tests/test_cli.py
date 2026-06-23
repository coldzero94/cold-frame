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


def test_cli_export_events_ndjson(
    cli_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["add", "a fact for the log"])
    capsys.readouterr()
    out = tmp_path / "events.ndjson"
    assert main(["export", str(out), "--events"]) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines and lines[0]["op"] == "create" and "event_id" in lines[0]
