"""Local web UI server — stdlib ``http.server``, serving the built Vue SPA + a read-only JSON API.

Dep-free + core-only (no Node, no web framework at runtime, I9): serves the CI-built SPA bundle
from ``_dist/`` (history-fallback for client routes) and degrades to a dependency-free inline
inspector when no bundle is present (dev without ``pnpm build``). The API exposes the "what I know
about you now" list + growth bands, the per-note MemoryField viz feed, and fact detail
(provenance + edges). Read-only for P3 — mutations (pin/forget/edit) are P4 Triage.

Security contract (security-spec §3): binds 127.0.0.1 only; a Host-header allowlist defends
DNS-rebinding; a strict same-origin CSP (``default-src 'self'``, no remote/inline script) ships
with every response (the inline dev fallback uses a scoped relaxed CSP); the port auto-falls-back
to the next free one (recorded in ``~/.cold-frame/ui.port`` so deep-links never go stale). CSRF
token + Origin allowlist land with the P4 mutating routes.
"""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from cold_frame.api import Memory
from cold_frame.branding import UI_HOST, UI_PORT, UI_PORTFILE
from cold_frame.exceptions import NoteNotFound
from cold_frame.models import Note
from cold_frame.read.strength import compute_strength

_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1"})
_BAND_GLYPH = {"evergreen": "🌳", "budding": "🌿", "fading": "🌱"}

# The CI-built SPA bundle (Vite output, shipped in the wheel via [tool.hatch ... artifacts]).
_DIST = Path(__file__).parent / "_dist"

# security-spec §3: every asset is same-origin, so a strict CSP holds for the shipped SPA —
# no remote script/style, no inline (the drive-by write-API protection, H8). connect-src 'self'
# keeps the JSON API same-origin; frame-ancestors/base-uri 'none' block clickjacking/base-tag.
_STRICT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)
# The inline dev inspector (served ONLY when no bundle is built) needs its one inline script +
# style, so it relaxes script/style to 'unsafe-inline'. Scoped to the degraded no-build path.
_FALLBACK_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def _spa_built() -> bool:
    """True iff a real Vite bundle (not just the .gitkeep placeholder) is present."""
    return (_DIST / "index.html").is_file()


# ── payload builders (pure; testable without HTTP) ───────────────────────────
def _note_brief(memory: Memory, note: Note) -> dict[str, Any]:
    s = compute_strength(note, memory._clock.now())
    return {
        "id": note.id,
        "content": note.content,
        "memory_type": note.memory_type,
        "status": note.status,
        "confidence": note.confidence,
        "strength": {"value": round(s.value, 3), "band": s.band, "at_risk": s.at_risk},
    }


def notes_payload(memory: Memory) -> dict[str, Any]:
    notes = memory.list_active(limit=200)
    return {"notes": [_note_brief(memory, n) for n in notes]}


def memory_field_payload(memory: Memory) -> dict[str, Any]:
    """The compact per-note shape the p5.js MemoryField hero viz consumes (matches
    ``prototype/gen_sample.py``): position is derived client-side from ``id`` (spatial-memory
    law), heat from ``s``/``band``, flicker from ``atRisk``, the glass frame from ``pinned``."""
    now = memory._clock.now()
    out: list[dict[str, Any]] = []
    for n in memory.list_active(limit=200):
        s = compute_strength(n, now)
        last = n.last_accessed or n.created_at
        out.append(
            {
                "id": n.id,
                "content": n.content,
                "type": n.memory_type,
                "s": round(s.value, 4),
                "band": s.band,
                "atRisk": s.at_risk,
                "importance": n.importance,
                "access": n.access_count,
                "pinned": n.pinned,
                "ageDays": max(0, (now - last).days),
            }
        )
    return {"notes": out}


def fact_payload(memory: Memory, fact_id: str) -> dict[str, Any] | None:
    try:
        note = memory.get(fact_id)
    except NoteNotFound:
        return None
    brief = _note_brief(memory, note)
    brief["sources"] = [
        {"kind": s.kind, "ref": s.ref, "role": s.role, "observed_at": s.observed_at.isoformat()}
        for s in note.sources
    ]
    brief["valid_at"] = note.valid_at.isoformat() if note.valid_at else None
    brief["edges"] = [
        {"src": e.src_id, "dst": e.dst_id, "relation": e.relation}
        for e in memory.neighbors(fact_id)
    ]
    return brief


# ── server + handler ─────────────────────────────────────────────────────────
class _UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], memory: Memory) -> None:
        super().__init__(addr, _Handler)
        self.memory = memory


class _Handler(BaseHTTPRequestHandler):
    def _server(self) -> _UIServer:
        return cast("_UIServer", self.server)

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if ":" in host else host
        return hostname in _ALLOWED_HOSTS

    def do_GET(self) -> None:  # stdlib http.server hook name (camelCase by API)
        if not self._host_allowed():  # DNS-rebind guard
            self._json(403, {"error": "forbidden host"})
            return
        path = urlparse(self.path).path
        memory = self._server().memory
        if path == "/api/notes":
            self._json(200, notes_payload(memory))
        elif path == "/api/memory-field":
            self._json(200, memory_field_payload(memory))
        elif path.startswith("/api/fact/"):
            data = fact_payload(memory, path[len("/api/fact/") :])
            self._json(200, data) if data is not None else self._json(404, {"error": "not_found"})
        elif path == "/api/health":
            self._json(200, dict(memory.health()))
        elif path.startswith("/api/"):
            self._json(404, {"error": "not_found"})
        else:  # the SPA (built bundle) or the inline inspector fallback
            self._serve_app(path)

    def _serve_app(self, path: str) -> None:
        if _spa_built():
            self._serve_static(path)
        else:  # no bundle (dev without `pnpm build`) → the degraded inline inspector
            self._html(_INDEX_HTML, csp=_FALLBACK_CSP)

    def _serve_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        root = _DIST.resolve()
        target = (root / rel).resolve()
        if not (target == root or target.is_relative_to(root)):  # path-traversal guard
            self._json(403, {"error": "forbidden"})
            return
        if not target.is_file():  # SPA history fallback: client routes resolve to index.html
            target = root / "index.html"
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), ctype, _STRICT_CSP)

    def _json(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, body, "application/json; charset=utf-8", _STRICT_CSP)

    def _html(self, html: str, *, csp: str = _STRICT_CSP) -> None:
        self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8", csp)

    def _send_bytes(self, code: int, body: bytes, ctype: str, csp: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence default stderr access logging
        pass


def bind(memory: Memory, *, host: str = UI_HOST, port: int = UI_PORT, tries: int = 50) -> _UIServer:
    """Bind a UI server on the first free port at/after ``port`` (auto-fallback)."""
    last: OSError | None = None
    for candidate in range(port, port + tries):
        try:
            return _UIServer((host, candidate), memory)
        except OSError as exc:  # port in use → try the next
            last = exc
    raise OSError(f"no free port in [{port}, {port + tries})") from last


def serve(
    memory: Memory,
    *,
    host: str = UI_HOST,
    port: int = UI_PORT,
    on_ready: Callable[[int], None] | None = None,
) -> None:
    """Run the local UI (blocks). Writes the resolved port to ``ui.port`` for deep-links."""
    server = bind(memory, host=host, port=port)
    resolved = server.server_address[1]
    UI_PORTFILE.parent.mkdir(parents=True, exist_ok=True)
    UI_PORTFILE.write_text(str(resolved), encoding="utf-8")
    if on_ready is not None:
        on_ready(resolved)
    try:
        server.serve_forever()
    finally:
        server.server_close()


_INDEX_HTML = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>cold-frame</title><style>
:root{color-scheme:dark}
body{margin:0;background:#0b0b0f;color:#e7e7ea;font:14px/1.5 -apple-system,Inter,sans-serif}
header{padding:20px 24px;border-bottom:1px solid #1c1c22}
h1{margin:0;font-size:15px;letter-spacing:.04em;color:#a9a9b2;font-weight:600}
main{padding:16px 24px;max-width:760px}
.card{padding:12px 14px;border:1px solid #1c1c22;border-radius:10px;margin:8px 0;background:#101015}
.c{display:flex;gap:10px;align-items:baseline}.g{font-size:16px}.m{color:#6f6f78;font-size:12px}
.bar{height:3px;border-radius:2px;background:#7C5CFF;margin-top:8px}
.risk{color:#e0795b;font-size:11px;margin-left:6px}.empty{color:#6f6f78}
</style></head><body>
<header><h1>COLD-FRAME · what I know about you now</h1></header>
<main id="app"><p class="empty">loading…</p></main><script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>(
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
fetch('/api/notes').then(r=>r.json()).then(d=>{
  const a=document.getElementById('app');
  if(!d.notes.length){a.innerHTML='<p class="empty">No memories yet.</p>';return}
  const glyph={evergreen:'\\u{1F333}',budding:'\\u{1F33F}',fading:'\\u{1F331}'};
  a.innerHTML=d.notes.map(n=>{
    const s=n.strength, g=glyph[s.band]||'\\u00B7';
    const risk=s.at_risk?'<span class="risk">\\u25CB at risk</span>':'';
    const w=Math.round(s.value*100);
    return '<div class="card"><div class="c"><span class="g">'+g+'</span><span>'+
      esc(n.content)+'</span>'+risk+'</div>'+
      '<div class="bar" style="width:'+w+'%"></div>'+
      '<div class="m">'+esc(n.memory_type)+' \\u00B7 S='+esc(s.value)+
      ' \\u00B7 conf='+esc(n.confidence)+'</div></div>';
  }).join('');
}).catch(e=>{document.getElementById('app').innerHTML='<p class="empty">error: '+esc(e)+'</p>'});
</script></body></html>"""
