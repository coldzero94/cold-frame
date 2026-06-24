"""Local web UI tests (P3 unit 5b): read-only API payloads + server + security."""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.cli import main as cli_main
from cold_frame.ui import server as ui


def test_notes_payload_shape(memory: Memory) -> None:
    memory.add("I prefer dark roast coffee")
    payload = ui.notes_payload(memory)
    assert len(payload["notes"]) == 1
    assert payload["total"] == 1  # full active count, for the "N of M" indicator
    note = payload["notes"][0]
    assert note["content"] == "I prefer dark roast coffee"
    assert note["strength"]["band"] in {"evergreen", "budding", "fading"}
    assert 0.0 <= note["strength"]["value"] <= 1.0


def test_payloads_cap_render_but_report_full_total(
    memory: Memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # render-capped list + true total → the client shows "N of M", never silently drops the tail.
    monkeypatch.setattr(ui, "_FIELD_CAP", 2)
    monkeypatch.setattr(ui, "_INSPECTOR_CAP", 2)
    for t in ("alpha fact", "beta fact", "gamma fact"):
        memory.add(t)
    field = ui.memory_field_payload(memory)
    notes = ui.notes_payload(memory)
    assert field["total"] == 3 and len(field["notes"]) == 2
    assert notes["total"] == 3 and len(notes["notes"]) == 2


def test_memory_field_payload_shape(memory: Memory) -> None:
    fid = memory.add("I prefer dark roast coffee").added[0].id
    payload = ui.memory_field_payload(memory)
    assert len(payload["notes"]) == 1
    assert payload["total"] == 1
    n = payload["notes"][0]
    # the EXACT shape the p5 MemoryField sketch consumes (prototype/gen_sample.py)
    assert set(n) == {
        "id", "content", "type", "s", "band", "atRisk",
        "importance", "access", "pinned", "ageDays",
    }
    assert n["id"] == fid and n["content"] == "I prefer dark roast coffee"
    assert n["type"] in {"semantic", "episodic", "procedural"}
    assert n["band"] in {"evergreen", "budding", "fading"}
    assert isinstance(n["atRisk"], bool) and isinstance(n["pinned"], bool)
    assert 0.0 <= n["s"] <= 1.0 and n["ageDays"] >= 0


def test_memory_field_payload_reflects_pinned(memory: Memory) -> None:
    fid = memory.add("keep me warm under glass").added[0].id
    memory.pin(fid)  # the field viz keys the cold-frame hexagon on this
    assert ui.memory_field_payload(memory)["notes"][0]["pinned"] is True


def test_memory_field_payload_threads_fading_and_at_risk(memory: Memory) -> None:
    fid = memory.add("a decayed, low-confidence memory").added[0].id
    now = memory._clock.now()
    # paint a decayed/stale/low-confidence note so strength → fading band + at_risk overlay
    memory._store._conn.execute(
        "UPDATE notes SET importance=?, decay_S=?, last_accessed=?, confidence=? WHERE id=?",
        (0.1, 5.0, (now - timedelta(days=120)).isoformat(), 0.2, fid),
    )
    memory._store._conn.commit()
    n = ui.memory_field_payload(memory)["notes"][0]
    assert n["band"] == "fading" and n["atRisk"] is True  # the wiring, not the formula
    assert n["ageDays"] >= 120  # threaded off last_accessed


def test_memory_field_age_days_uses_created_at_and_clamps(memory: Memory) -> None:
    now = memory._clock.now()
    # (a) no last_accessed → ageDays computed off created_at
    a = memory.add("never re-accessed").added[0].id
    memory._store._conn.execute(
        "UPDATE notes SET created_at=?, last_accessed=NULL WHERE id=?",
        ((now - timedelta(days=30)).isoformat(), a),
    )
    # (b) last_accessed in the FUTURE (clock skew) → negative delta → clamped to 0
    b = memory.add("a future-stamped note").added[0].id
    memory._store._conn.execute(
        "UPDATE notes SET last_accessed=? WHERE id=?", ((now + timedelta(days=3)).isoformat(), b)
    )
    memory._store._conn.commit()
    by_id = {n["id"]: n for n in ui.memory_field_payload(memory)["notes"]}
    assert by_id[a]["ageDays"] == 30  # created_at fallback used
    assert by_id[b]["ageDays"] == 0  # max(0, negative) clamp held


def test_csp_split_is_scoped_to_script_and_style_only() -> None:
    strict, fallback = ui._STRICT_CSP, ui._FALLBACK_CSP
    assert strict != fallback
    assert "'unsafe-inline'" not in strict  # the strict path never allows inline

    def directive(csp: str, name: str) -> str:
        for part in (p.strip() for p in csp.split(";")):
            if part.startswith(name + " "):
                return part
        return ""

    # the fallback relaxes ONLY script-src AND style-src (both, not just one) to 'unsafe-inline'
    assert "'unsafe-inline'" in directive(fallback, "script-src")
    assert "'unsafe-inline'" in directive(fallback, "style-src")
    # every OTHER directive is byte-identical → the relaxation never widened connect/frame/base
    for name in ("default-src", "img-src", "connect-src", "frame-ancestors", "base-uri"):
        assert directive(strict, name) == directive(fallback, name)
        assert "'unsafe-inline'" not in directive(fallback, name)


def test_fact_payload_includes_provenance_and_unknown_is_none(memory: Memory) -> None:
    fid = memory.add("I prefer dark roast coffee").added[0].id
    fact = ui.fact_payload(memory, fid)
    assert fact is not None
    assert fact["content"] == "I prefer dark roast coffee"
    assert fact["sources"] and fact["sources"][0]["kind"] == "message"
    assert "edges" in fact
    assert ui.fact_payload(memory, "does-not-exist") is None


@pytest.fixture
def running_ui(
    memory: Memory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[int]:
    # Deterministic: pin _DIST to a bundle-less dir so these tests exercise the inline fallback
    # regardless of whether `pnpm build` has populated the real _dist. (SPA mode has its own test.)
    monkeypatch.setattr(ui, "_DIST", tmp_path / "no_bundle")
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


def test_memory_field_route(running_ui: int) -> None:
    resp = urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/api/memory-field")
    assert resp.status == 200
    data = json.loads(resp.read())
    n = data["notes"][0]
    assert {"id", "s", "band", "atRisk", "pinned", "ageDays"} <= set(n)


def test_strict_csp_on_api(running_ui: int) -> None:
    resp = urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/api/notes")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    assert "unsafe-inline" not in csp  # strict on the API surface
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def _run_server(memory: Memory) -> tuple[ui._UIServer, int]:
    server = ui.bind(memory, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_static_spa_serving_history_fallback_and_strict_csp(
    memory: Memory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "_dist"
    dist.mkdir()
    (dist / "index.html").write_text('<!doctype html><div id=app>SPA</div><script src="/a.js">')
    (dist / "a.js").write_text("console.log('app')")
    (dist / "s.css").write_text("body{color:#fff}")
    (dist / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header
    monkeypatch.setattr(ui, "_DIST", dist)  # pretend a real bundle is built
    server, port = _run_server(memory)

    def ctype(p: str) -> str:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}{p}").headers["Content-Type"]

    try:
        root = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert b"SPA" in root.read()
        csp = root.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp and "unsafe-inline" not in csp  # strict for the SPA
        asset = urllib.request.urlopen(f"http://127.0.0.1:{port}/a.js")
        assert b"console.log" in asset.read()
        assert "javascript" in asset.headers["Content-Type"]
        assert "default-src 'self'" in asset.headers["Content-Security-Policy"]  # strict on assets
        # mimetypes: CSS must be text/css+charset (else nosniff refuses it); PNG binary, no charset
        assert ctype("/s.css") == "text/css; charset=utf-8"
        assert ctype("/icon.png") == "image/png"
        # a client-side route (no such file) falls back to index.html (SPA history routing)
        deep = urllib.request.urlopen(f"http://127.0.0.1:{port}/fact/abc")
        assert b"SPA" in deep.read()
    finally:
        server.shutdown()
        server.server_close()


def test_fact_deeplink_path_has_a_matching_router_route() -> None:
    # The server's history-fallback returns 200 for ANY path, so a server test can't catch a
    # deep-link that the CLIENT router doesn't define (it renders a blank <main>). Guard the
    # cross-language contract directly: branding.fact_deeplink's path must be a route in router.ts.
    from urllib.parse import urlparse

    from cold_frame import branding

    path = urlparse(branding.fact_deeplink("SOME_ID")).path  # e.g. /fact/SOME_ID
    template = path.replace("SOME_ID", ":id")  # /fact/:id
    router = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "router.ts").read_text()
    assert f"'{template}'" in router, f"router.ts defines no route for deeplink path {template!r}"


def test_inline_fallback_when_no_bundle(running_ui: int) -> None:
    # the test env has no built bundle → '/' serves the dependency-free inline inspector
    resp = urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/")
    assert b"cold-frame" in resp.read().lower()
    assert "unsafe-inline" in resp.headers["Content-Security-Policy"]  # scoped relax for inline


def test_static_serving_rejects_path_traversal(
    memory: Memory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a real bundle must be present, else _serve_static (and its traversal guard) is never reached
    dist = tmp_path / "_dist"
    dist.mkdir()
    (dist / "index.html").write_text("<div id=app>SPA</div>")
    (tmp_path / "secret.txt").write_text("OUTSIDE-ROOT")  # sibling of _dist, must never leak
    monkeypatch.setattr(ui, "_DIST", dist)
    server, port = _run_server(memory)
    try:
        # raw http.client: urllib normalizes the ../ away client-side, so the guard never sees it
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("GET", "/../secret.txt", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 403  # path-traversal guard (server.py _serve_static)
        assert b"OUTSIDE-ROOT" not in body  # the sibling file is never disclosed
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "check_ui_bundle.py"


def _run_guard(tmp: Path, *, index: str | None) -> subprocess.CompletedProcess[str]:
    """Run the release guard against a temp tree (only .gitkeep, or an index.html)."""
    dist = tmp / "cold_frame" / "ui" / "_dist"
    dist.mkdir(parents=True)
    (dist / ".gitkeep").touch()
    if index is not None:
        (dist / "index.html").write_text(index)
    (tmp / "scripts").mkdir()
    (tmp / "scripts" / "check_ui_bundle.py").write_text(_GUARD.read_text())
    return subprocess.run(
        [sys.executable, "scripts/check_ui_bundle.py"], cwd=tmp, capture_output=True, text=True
    )


def test_ui_bundle_guard_fails_on_placeholder_and_passes_on_real(tmp_path: Path) -> None:
    # only .gitkeep → loud fail (a release would ship a blank UI)
    miss = _run_guard(tmp_path / "a", index=None)
    assert miss.returncode != 0 and "UI bundle missing" in (miss.stdout + miss.stderr)
    # index.html without the asset graph → still fails (a half-built bundle)
    half = _run_guard(tmp_path / "b", index="<html>no assets</html>")
    assert half.returncode != 0
    # a real bundle (references assets/) → passes
    ok = _run_guard(tmp_path / "c", index='<script src="/assets/index.js"></script>')
    assert ok.returncode == 0


def test_server_rejects_foreign_host_header(running_ui: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{running_ui}/api/notes", headers={"Host": "evil.example.com"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 403  # DNS-rebind guard


def test_unknown_api_path_is_404_but_client_route_is_spa(running_ui: int) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/api/nope")  # unknown API → 404
    assert exc.value.code == 404
    # a non-API path is a client-side route, NOT a 404 (SPA history routing → app shell)
    assert urllib.request.urlopen(f"http://127.0.0.1:{running_ui}/triage").status == 200


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
