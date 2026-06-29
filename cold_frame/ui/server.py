"""Local web UI server — stdlib ``http.server``, serving the built Vue SPA + a JSON API (read +
CSRF-guarded mutations).

Dep-free + core-only (no Node, no web framework at runtime, I9): serves the CI-built SPA bundle
from ``_dist/`` (history-fallback for client routes) and degrades to a dependency-free inline
inspector when no bundle is present (dev without ``pnpm build``). The API exposes the "what I know
about you now" list, the per-note MemoryField viz feed, fact detail (provenance + edges), search,
belief history, and health (GET); plus mutating routes (pin/forget/revive/correct/create/triage).

Security contract (security-spec §3 + §localhost): binds 127.0.0.1 only; a Host-header allowlist
defends DNS-rebinding; a strict same-origin CSP (``default-src 'self'``, no remote/inline script)
ships with every response (the inline dev fallback uses a scoped relaxed CSP); the port auto-falls
-back to the next free one (recorded in ``~/.cold-frame/ui.port``). Every mutating request requires
BOTH a same-origin ``Origin`` (fail-closed if absent) and the per-process CSRF token (injected into
the page, sent back as ``X-CSRF-Token``) — a drive-by site can neither forge the origin nor read it.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

from cold_frame.api import Memory
from cold_frame.branding import UI_HOST, UI_PORT, UI_PORTFILE
from cold_frame.exceptions import ColdFrameError, NoteNotFound, SecretBlocked
from cold_frame.models import Note
from cold_frame.observability import get_logger
from cold_frame.read.strength import compute_strength
from cold_frame.ui.contract import (
    CreateFactResponse,
    FactDetailDict,
    FactHistoryResponse,
    FieldNoteDict,
    MemoryFieldResponse,
    NoteBriefDict,
    NotesResponse,
    SearchHitDict,
    SearchResponse,
    TriageItemDict,
    TriageResolveResponse,
    TriageResponse,
)

_log = get_logger(__name__)

# DNS-rebind allowlist tracks the bind address (branding indirection, §4): if UI_HOST moves, the
# allowlist moves with it. ``localhost`` is a stable second alias for the same loopback bind.
_ALLOWED_HOSTS = frozenset({"localhost", UI_HOST})

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


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO date/datetime from a query param → tz-aware UTC, or None if empty/invalid."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ── payload builders (pure; testable without HTTP) ───────────────────────────
# Wire shapes live in contract.py (the single source of truth the TS client is generated from).
def _note_brief(memory: Memory, note: Note) -> NoteBriefDict:
    s = compute_strength(note, memory._clock.now())
    return {
        "id": note.id,
        "content": note.content,
        "memory_type": note.memory_type,
        "status": note.status,
        "confidence": note.confidence,
        "strength": {"value": round(s.value, 3), "band": s.band, "at_risk": s.at_risk},
    }


# Active set is bounded by the per-scope caps (≤2600), so fetching "all" to count is cheap on a
# local single-user DB. We render a capped prefix and report the true total → no silent truncation.
_ACTIVE_FETCH = 5000  # > sum of caps → effectively all active, for an exact count
_INSPECTOR_CAP = 500  # list render cap; UI shows "N of M" when total exceeds it
_FIELD_CAP = 600  # field-ember render cap (a density control lands in a later phase)


def notes_payload(memory: Memory) -> NotesResponse:
    active = memory.list_active(limit=_ACTIVE_FETCH)
    shown = active[:_INSPECTOR_CAP]
    return {"notes": [_note_brief(memory, n) for n in shown], "total": len(active)}


def memory_field_payload(memory: Memory) -> MemoryFieldResponse:
    """The compact per-note shape the p5.js MemoryField hero viz consumes (matches
    ``prototype/gen_sample.py``): position is derived client-side from ``id`` (spatial-memory
    law), heat from ``s``/``band``, flicker from ``atRisk``, the glass frame from ``pinned``."""
    now = memory._clock.now()
    active = memory.list_active(limit=_ACTIVE_FETCH)
    out: list[FieldNoteDict] = []
    for n in active[:_FIELD_CAP]:
        s = compute_strength(n, now)
        last = n.last_accessed or n.created_at  # fresh notes have no last_accessed
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
                "ageDays": max(0, (now - last).days),  # clamp negative clock skew
            }
        )
    return {"notes": out, "total": len(active)}


def fact_payload(memory: Memory, fact_id: str) -> FactDetailDict | None:
    try:
        note = memory.get(fact_id)
    except NoteNotFound:
        return None
    brief = _note_brief(memory, note)
    return {
        **brief,
        "sources": [
            {"kind": s.kind, "ref": s.ref, "role": s.role, "observed_at": s.observed_at.isoformat()}
            for s in note.sources
        ],
        "valid_at": note.valid_at.isoformat() if note.valid_at else None,
        "edges": [
            {"src": e.src_id, "dst": e.dst_id, "relation": e.relation}
            for e in memory.neighbors(fact_id)
        ],
        "accesses": [t.isoformat() for t in memory.access_history(fact_id)],
    }


def search_payload(
    memory: Memory, query: str, *, k: int = 20, as_of: datetime | None = None
) -> SearchResponse:
    """Ranked hits for a query, each carrying its retrieval signal breakdown (explainability).

    ``as_of`` → time-travel: search the belief state as it was at that instant (bi-temporal
    valid_at≤T<invalid_at), incl. notes archived SINCE T (``include_archived``) — "what did I
    believe in March". ``reinforce=False``: a UI search is a human browsing, not the agent
    recalling — it must not bump decay/access (and keeps this ungated GET from being a write)."""
    result = memory.search(
        query, k=k, reinforce=False, as_of=as_of, include_archived=as_of is not None
    )
    hits: list[SearchHitDict] = []
    for h in result.hits:
        sig = h.signals
        hits.append(
            {
                **_note_brief(memory, h.note),
                "score": round(h.score, 4),
                "signals": {
                    "semantic": sig.semantic,
                    "bm25": sig.bm25,
                    "edge": sig.edge,
                    "rrf": round(sig.rrf, 4),
                    "rerank": sig.rerank,
                },
            }
        )
    return {"query": query, "hits": hits}


def triage_payload(memory: Memory) -> TriageResponse:
    """The human-resolution queue: notes held for review (low-confidence / conflict / merge)."""
    items: list[TriageItemDict] = []
    for it in memory.triage_queue():
        items.append(
            {
                **_note_brief(memory, it.note),
                "reason": it.reason,
                "candidates": list(it.candidates),
                "impact": round(it.impact, 4),
            }
        )
    return {"items": items}


def fact_history_payload(memory: Memory, fact_id: str) -> FactHistoryResponse | None:
    """Every persisted version of a note (oldest→newest) — the rewindable belief trail."""
    try:
        versions = memory.fork_history(fact_id)
    except NoteNotFound:
        return None
    return {
        "versions": [
            {
                "id": v.id,
                "content": v.content,
                "status": v.status,
                "version": v.version,
                "valid_at": v.valid_at.isoformat() if v.valid_at else None,
                "invalid_at": v.invalid_at.isoformat() if v.invalid_at else None,
            }
            for v in versions
        ]
    }


# ── server + handler ─────────────────────────────────────────────────────────
class _UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], memory: Memory) -> None:
        super().__init__(addr, _Handler)
        self.memory = memory
        # per-process CSRF token (security-spec §localhost): injected into the served page, required
        # on every mutating request alongside a same-origin Origin check. Unauthenticated,
        # not defenceless — a drive-by site can neither read this token nor forge Origin.
        self.csrf_token = secrets.token_urlsafe(32)

    def allowed_origins(self) -> frozenset[str]:
        port = self.server_address[1]
        return frozenset(f"http://{h}:{port}" for h in _ALLOWED_HOSTS)

    def handle_error(self, request: object, client_address: object) -> None:
        # Override the stdlib default (raw traceback to stderr) so an unexpected handler error logs
        # only the exception TYPE through the redacting logger — never a path or content (I16).
        exc = sys.exc_info()[1]
        _log.warning("ui_handler_error", extra={"exc_type": type(exc).__name__})


class _Handler(BaseHTTPRequestHandler):
    def _server(self) -> _UIServer:
        return cast("_UIServer", self.server)

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if ":" in host else host
        return hostname in _ALLOWED_HOSTS

    def _failsafe(self, exc: BaseException) -> None:
        # never drop the connection on an unhandled error: log the TYPE only (I16) + send a real 500
        # so the client sees a structured error instead of "Failed to fetch" on a reset socket.
        _log.warning("ui_unhandled", extra={"exc_type": type(exc).__name__})
        with suppress(Exception):
            self._json(500, {"error": "internal"})

    def do_GET(self) -> None:  # stdlib http.server hook name (camelCase by API)
        if not self._host_allowed():  # DNS-rebind guard
            self._json(403, {"error": "forbidden host"})
            return
        try:
            self._route_get()
        except Exception as exc:  # a StoreError in a payload builder → 500, not a reset socket
            self._failsafe(exc)

    def _route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        memory = self._server().memory
        if path == "/api/notes":
            self._json(200, notes_payload(memory))
        elif path == "/api/memory-field":
            self._json(200, memory_field_payload(memory))
        elif path == "/api/search":
            qs = parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0].strip()
            as_of = _parse_iso((qs.get("as_of") or [""])[0])  # optional time-travel point
            try:  # optional ?k= result count, clamped to a sane range
                k = max(1, min(100, int((qs.get("k") or ["20"])[0])))
            except ValueError:
                k = 20
            res = search_payload(memory, q, k=k, as_of=as_of) if q else {"query": "", "hits": []}
            self._json(200, res)
        elif path.startswith("/api/fact/") and path.endswith("/history"):
            fid = path[len("/api/fact/") : -len("/history")]
            hist = fact_history_payload(memory, fid)
            if hist is not None:
                self._json(200, hist)
            else:
                self._json(404, {"error": "not_found"})
        elif path.startswith("/api/fact/"):
            data = fact_payload(memory, path[len("/api/fact/") :])
            if data is not None:
                self._json(200, data)
            else:
                self._json(404, {"error": "not_found"})
        elif path == "/api/triage":
            self._json(200, triage_payload(memory))
        elif path == "/api/health":
            self._json(200, dict(memory.health()))
        elif path.startswith("/api/"):
            self._json(404, {"error": "not_found"})
        else:  # the SPA (built bundle) or the inline inspector fallback
            self._serve_app(path)

    # ── mutating requests (security-spec §localhost: Host + same-origin Origin + CSRF token) ──
    def _csrf_ok(self) -> bool:
        srv = self._server()
        # Require a same-origin Origin: browsers always send it on a POST (same- OR cross-origin),
        # and it cannot be forged by a drive-by page — so fail CLOSED if it's absent or foreign
        # (no looser Referer fallback). The per-process token is the second factor; both must pass.
        if self.headers.get("Origin") not in srv.allowed_origins():
            return False
        token = self.headers.get("X-CSRF-Token", "")
        return bool(token) and secrets.compare_digest(token, srv.csrf_token)

    def _read_json_body(self) -> dict[str, object]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 1_000_000:  # bound the body — a local UI never posts a megabyte
            raise ValueError("body too large")
        data = json.loads(self.rfile.read(n))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _note_result(self, fn: Callable[[], Note]) -> None:
        try:
            note = fn()
        except NoteNotFound:
            self._json(404, {"error": "not_found"})
        except SecretBlocked:  # user-actionable (a secret in the text) — NOT an internal error
            self._json(422, {"error": "secret_blocked"})
        except ValueError:
            self._json(400, {"error": "invalid"})
        except ColdFrameError as exc:  # StoreError / EmbedderMismatch / … → a real 500, not a drop
            _log.warning("ui_write_failed", extra={"exc_type": type(exc).__name__})  # type only
            self._json(500, {"error": "internal"})
        else:
            self._json(200, _note_brief(self._server().memory, note))

    def do_POST(self) -> None:  # stdlib hook name
        if not self._host_allowed():  # DNS-rebind guard (same as GET)
            self._json(403, {"error": "forbidden host"})
            return
        if not self._csrf_ok():  # same-origin Origin + per-process token — blocks drive-by writes
            self._json(403, {"error": "csrf_failed"})
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self._json(400, {"error": "bad_request"})
            return
        try:
            self._route_post(urlparse(self.path).path, self._server().memory, body)
        except Exception as exc:  # an unguarded create_fact StoreError → 500, not a reset socket
            self._failsafe(exc)

    def _route_post(self, path: str, memory: Memory, body: dict[str, object]) -> None:
        if path.startswith("/api/fact/") and "/" in path[len("/api/fact/") :]:
            fid, _, action = path[len("/api/fact/") :].partition("/")
            if action == "pin":
                self._note_result(lambda: memory.pin(fid))
            elif action == "forget":
                self._note_result(lambda: memory.forget(fid))
            elif action == "revive":
                self._note_result(lambda: memory.revive(fid))
            elif action == "correct":
                raw = body.get("text")
                text = raw.strip() if isinstance(raw, str) else ""
                if not text:  # reject non-string/empty rather than str()-coercing null→"None"
                    self._json(400, {"error": "text_required"})
                else:
                    self._note_result(lambda: memory.correct_memory(fid, text).new)
            else:
                self._json(404, {"error": "not_found"})
        elif path == "/api/fact":  # create a new fact (same WriteCore as add, I15)
            raw = body.get("text")
            text = raw.strip() if isinstance(raw, str) else ""
            mt = body.get("memory_type", "semantic")
            if not text:
                self._json(400, {"error": "text_required"})
            elif mt not in ("semantic", "episodic", "procedural"):
                self._json(400, {"error": "bad_memory_type"})
            else:
                res = memory.create_fact(text, memory_type=mt)
                added = res.added[0] if res.added else None
                create_resp: CreateFactResponse = {
                    "added": _note_brief(memory, added) if added else None,
                    "deduped": res.deduped,
                    # surface a pre-disk secret-BLOCK (I6) so the form can tell the user why
                    # nothing was stored, instead of silently showing no change.
                    "blocked": [b.placeholder for b in res.blocked],
                }
                self._json(200, create_resp)
        elif path.startswith("/api/triage/") and path.endswith("/resolve"):
            tid = path[len("/api/triage/") : -len("/resolve")]
            act = body.get("action")
            target = body.get("target")
            if act not in ("pin", "let_go", "merge", "keep", "supersede"):
                self._json(400, {"error": "bad_action"})
            else:
                try:
                    memory.resolve_triage(
                        tid,
                        act,
                        target=str(target) if target is not None else None,
                    )
                except NoteNotFound:
                    self._json(404, {"error": "not_found"})
                except ValueError:
                    self._json(400, {"error": "invalid"})
                else:
                    triage_resp: TriageResolveResponse = {"ok": True}
                    self._json(200, triage_resp)
        else:
            self._json(404, {"error": "not_found"})

    def _serve_app(self, path: str) -> None:
        if _spa_built():
            self._serve_static(path)
        else:  # no bundle (dev without `pnpm build`) → the degraded inline inspector
            self._html(self._inject_csrf(_INDEX_HTML), csp=_FALLBACK_CSP)

    def _inject_csrf(self, html: str) -> str:
        # surface the per-process CSRF token to the SPA via a meta tag (security-spec §localhost).
        tag = f'<meta name="csrf-token" content="{self._server().csrf_token}">'
        return html.replace("<head>", "<head>" + tag, 1) if "<head>" in html else tag + html

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
        try:
            body = target.read_bytes()
        except OSError as exc:  # TOCTOU/permission/IO — fail with a real 500, not a dropped conn
            _log.warning("ui_static_read_failed", extra={"errno": exc.errno})  # id/errno only (I16)
            self._json(500, {"error": "asset_unavailable"})
            return
        if target.name == "index.html":  # inject the CSRF token into the SPA entry document
            body = self._inject_csrf(body.decode("utf-8")).encode("utf-8")
        self._send_bytes(200, body, ctype, _STRICT_CSP)

    def _json(self, code: int, obj: object) -> None:
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
        with suppress(BrokenPipeError, ConnectionError):  # client navigated away mid-response
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
<meta name="color-scheme" content="dark"><title>cold-frame</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"><style>
:root{color-scheme:dark}
body{margin:0;background:#0b0b0f;color:#e7e7ea;font:14px/1.5 -apple-system,Inter,sans-serif}
header{padding:20px 24px;border-bottom:1px solid #1c1c22}
h1{margin:0;font-size:15px;letter-spacing:.04em;color:#a9a9b2;font-weight:600}
main{padding:16px 24px;max-width:760px}
.card{padding:12px 14px;border:1px solid #1c1c22;border-radius:10px;margin:8px 0;background:#101015}
.c{display:flex;gap:10px;align-items:baseline}.g{font-size:16px}.m{color:#8a8a93;font-size:12px}
.bar{height:3px;border-radius:2px;background:#7C5CFF;margin-top:8px}
.risk{color:#e0795b;font-size:11px;margin-left:6px}.empty{color:#8a8a93}
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
