"""Local web UI tests (P3 unit 5b): read-only API payloads + server + security."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.cli import main as cli_main
from cold_frame.ui import server as ui


def test_notes_payload_shape(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    payload = ui.notes_payload(memory)
    assert len(payload["notes"]) == 1
    note = payload["notes"][0]
    assert note["content"] == "I prefer dark roast coffee"
    assert note["strength"]["band"] in {"evergreen", "budding", "fading"}
    assert 0.0 <= note["strength"]["value"] <= 1.0


def test_fact_payload_includes_provenance_and_unknown_is_none(memory: Memory) -> None:
    fid = memory.add("I prefer dark roast coffee").added[0].id
    fact = ui.fact_payload(memory, fid)
    assert fact is not None
    assert fact["content"] == "I prefer dark roast coffee"
    assert fact["sources"] and fact["sources"][0]["kind"] == "message"
    assert "edges" in fact
    assert ui.fact_payload(memory, "does-not-exist") is None


@pytest.fixture
def running_ui(memory: Memory) -> Iterator[int]:
    memory.add("I prefer dark roast coffee")
    server = ui.bind(memory, host="127.0.0.1", port=0)  # ephemeral free port
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def test_server_serves_notes_and_fact(running_ui: int) -> None:
    resp = urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/api/notes")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["notes"][0]["content"] == "I prefer dark roast coffee"

    html = urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/").read()
    assert b"cold-frame" in html.lower()


def test_server_rejects_foreign_host_header(running_ui: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{running_ui}/api/notes", headers={"Host": "evil.example.com"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 403  # DNS-rebind guard


def test_server_unknown_path_is_404(running_ui: int) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/nope")
    assert exc.value.code == 404


def test_bind_falls_back_when_port_taken(memory: Memory) -> None:
    taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    busy_port = taken.getsockname()[1]
    try:
        server = ui.bind(memory, host="127.0.0.1", port=busy_port)
        assert server.server_address[1] != busy_port  # auto-fallback to the next free port
        server.server_close()
    finally:
        taken.close()


def test_cli_ui_dispatches_to_serve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []
    monkeypatch.setenv("COLD_FRAME_DB", str(tmp_path / "ui.db"))
    monkeypatch.setattr(ui, "serve", lambda *a, **k: calls.append(1))
    assert cli_main(["ui", "--port", "0"]) == 0
    assert calls == [1]


def test_index_html_escapes_note_content_xss() -> None:
    # the inspector renders note content via an esc() helper, never raw innerHTML (stored XSS)
    html = ui._INDEX_HTML
    assert "esc(n.content)" in html  # user content is HTML-escaped before injection
    assert "+n.content+" not in html  # the raw-injection footgun is gone
    assert "const esc=" in html  # the escaper is defined
