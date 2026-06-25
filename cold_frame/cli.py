"""``cold-frame`` CLI (SPEC §9). Entry point: ``cold-frame = "cold_frame.cli:main"``.

P1 wires the offline path: ``add`` → ``search`` recalls the just-added fact with zero
keys/network (I5), plus ``doctor`` (invariant check) and ``mcp`` (dispatch to the stdio
server). Other subcommands remain stubs until their phase. The DB location resolves from
``--db`` → ``$COLD_FRAME_DB`` → ``branding.DB_PATH`` (no literal path strings, branding rule).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from cold_frame import __version__, branding
from cold_frame.api import Memory
from cold_frame.branding import PKG
from cold_frame.exceptions import NoteNotFound

_SUBCOMMANDS: tuple[str, ...] = (
    "add",
    "search",
    "list",
    "show",
    "stats",
    "timeline",
    "path",
    "doctor",
    "consolidate",
    "worker",
    "export",
    "import",
    "ui",
    "mcp",
    "setup",
    "purge",
    "reembed",
)


def _resolve_db(args: argparse.Namespace) -> str:
    return args.db or os.environ.get("COLD_FRAME_DB") or str(branding.DB_PATH)


_OPENED: list[Memory] = []  # memories opened this invocation, closed in main()'s finally


def _memory(args: argparse.Namespace) -> Memory:
    mem = Memory(_resolve_db(args))  # offline default: HashEmbedder + llm=None
    _OPENED.append(mem)  # tracked so the connection is closed before the process/command ends
    return mem


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"{PKG}: '{args.command}' not implemented")
    return 1


def _resolve_id(mem: Memory, prefix: str) -> str | None:
    """Resolve a (possibly 8-char-truncated) id to a full id, or None if absent/ambiguous.

    ``list``/``add`` print 8-char ids, so the CLI accepts a unique prefix. Exact ids
    resolve regardless of status (incl. archived); prefix-matching scans the active set.
    """
    try:
        return mem.get(prefix).id  # exact hit (any status)
    except NoteNotFound:
        matches = [n.id for n in mem.list_active(limit=1_000_000) if n.id.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None


def _cmd_add(args: argparse.Namespace) -> int:
    if not args.text:
        print(f'{PKG}: add requires text (usage: {PKG} add "...")')
        return 1
    res = _memory(args).add(args.text, raw=args.raw)
    for note in res.added:
        print(f"+ {note.id[:8]}  {note.content}")
    for note in res.held:
        print(f"~ {note.id[:8]}  (held for review)  {note.content}")
    if not res.added and not res.held:
        print("nothing extracted")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.query:
        print(f"{PKG}: search requires a query")
        return 1
    res = _memory(args).search(args.query, k=args.k)
    if not res.hits:
        print("no matches")
        return 0
    for hit in res.hits:
        print(f"{hit.score:.4f}  {hit.note.id[:8]}  {hit.note.content}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    mem = _memory(args)
    notes = mem.list_active(limit=args.limit)
    if not notes:
        print("no active memories")
        return 0
    for n in notes:
        band = mem.strength(n.id).band
        print(f"{n.id[:8]}  [{band:9}] {n.content[:80]}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not args.id:
        print("show: provide a note id")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    n = mem.get(resolved)
    print(f"id:       {n.id}")
    print(f"status:   {n.status}  type: {n.memory_type}  v{n.version}")
    print(f"content:  {n.content}")
    print(f"created:  {n.created_at}  valid_at: {n.valid_at}")
    print(f"conf={n.confidence}  importance={n.importance}  pinned={n.pinned}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    mem = _memory(args)
    h = mem.health()
    active = mem.list_active(limit=1_000_000)
    by_type = Counter(n.memory_type for n in active)
    print(f"notes={h['notes']}  active={len(active)}  fts={h['fts']}  vec={h['vec']}")
    print("by type: " + (", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "(none)"))
    return 0


def _cmd_timeline(args: argparse.Namespace) -> int:
    """Belief timeline of one note: every persisted version, oldest→newest (fork_history)."""
    if not args.id:
        print("timeline: provide a note id")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    versions = mem.fork_history(resolved)
    if not versions:
        print(f"no history for {resolved[:8]}")
        return 0
    print(f"timeline of {resolved[:8]}  ({len(versions)} version(s)):")
    for v in versions:
        mark = "▶" if v.status == "active" else "·"
        print(f"  {mark} v{v.version}  [{v.status:8}]  valid_at={v.valid_at}  {v.content[:60]}")
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    """Shortest edge path between two notes (bounded ego-lens BFS, undirected, ≤max-hops)."""
    mem = _memory(args)
    src = _resolve_id(mem, args.src)
    dst = _resolve_id(mem, args.dst)
    if src is None:
        print(f"not found (or ambiguous prefix): {args.src}")
        return 1
    if dst is None:
        print(f"not found (or ambiguous prefix): {args.dst}")
        return 1
    if src == dst:
        print(f"{src[:8]} (same note)")
        return 0
    # BFS over edges treated as undirected; each frontier item carries its edge trail.
    visited: set[str] = {src}
    frontier: list[tuple[str, list[str]]] = [(src, [])]
    for _hop in range(args.max_hops):
        nxt: list[tuple[str, list[str]]] = []
        for node, trail in frontier:
            for edge in mem.neighbors(node):
                other = edge.dst_id if edge.src_id == node else edge.src_id
                if other in visited:
                    continue
                arrow = "->" if edge.src_id == node else "<-"
                step = f"{arrow}[{edge.relation}] {other[:8]}"
                if other == dst:
                    print(f"{src[:8]} " + " ".join([*trail, step]))
                    return 0
                visited.add(other)
                nxt.append((other, [*trail, step]))
        if not nxt:
            break
        frontier = nxt
    print(f"no path within {args.max_hops} hop(s): {src[:8]} … {dst[:8]}")
    return 1


def _cmd_setup(args: argparse.Namespace) -> int:
    """First-run setup: create/migrate the DB and print the Claude Code wiring steps."""
    mem = _memory(args)  # constructing a Memory runs migrate() (idempotent)
    h = mem.health()
    print(f"{PKG}: ready.")
    print(f"  db:       {h['db_path']}")
    print(f"  embedder: {h['embedder_id']} (dim={h['dim']}, offline — no key, no network)")
    print()
    print("Connect to Claude Code (local MCP, stdio — no server, no OAuth):")
    print(f"  claude mcp add {branding.MCP_ID} -- {PKG} mcp")
    print()
    print("Try it:")
    print(f'  {PKG} add "I prefer dark roast coffee"')
    print(f'  {PKG} search "coffee"')
    print(f"  {PKG} ui        # browse your memory at {branding.ui_base_url()}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    """Hard-scrub a secret/PII note from every grain + VACUUM + grep-verify (NOT revivable)."""
    if not args.id:
        print("purge: provide a note id")
        return 1
    if not args.force:  # destructive + irreversible → never on a bare invocation
        print(f"purge permanently scrubs {args.id} (not revivable) — re-run with --force")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    report = mem.purge(resolved, cascade=args.cascade)
    status = "clean" if report.grep_clean else "RESIDUE FOUND"
    print(f"purged {resolved[:8]}: scrubbed {report.rows_scrubbed} row(s), grep={status}")
    return 0 if report.grep_clean else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    h = _memory(args).health()
    print(f"db: {h['db_path']}")
    print(f"notes={h['notes']} fts={h['fts']} vec={h['vec']}  (match={h['counts_match']})")
    print(f"integrity_check={h['integrity']}  fts_integrity={h['fts_integrity']}")
    print(f"embedder={h['embedder_id']} dim={h['dim']}  stale_vectors={h['stale_vectors']}")
    if h["stale_vectors"]:  # vectors from an old embedder → not semantically searchable yet
        print(f"  → run '{PKG} reembed' to re-index {h['stale_vectors']} stale vector(s)")
    print(f"ui_port={branding.UI_PORT}")
    ok = (
        bool(h["counts_match"])
        and h["integrity"] == "ok"
        and h["fts_integrity"] == "ok"
        and h["stale_vectors"] == 0
    )
    print("ok" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


# ── Claude Code hooks (auto-recall / capture, D26) ───────────────────────────
_RECALL_K = 7  # SessionStart digest: inject up to K strongest durable beliefs


def _settings_path(*, user: bool) -> Path:
    """Claude Code settings file: ~/.claude (user) or ./.claude (project)."""
    base = Path.home() if user else Path.cwd()
    return base / ".claude" / "settings.json"


def _cmd_hook_session_start(args: argparse.Namespace) -> int:
    """SessionStart recall hook: emit the strongest durable memories as additionalContext so a new
    Claude Code session opens already knowing the user. Read-only (no reinforce, no write); on ANY
    error it emits nothing and exits 0 — a hook must never block the session (D26)."""
    try:
        mem = _memory(args)
        lines: list[str] = []
        for n in mem.list_active(sort="importance", limit=_RECALL_K * 3):
            if mem.strength(n.id).band == "fading":
                continue  # inject durable beliefs, not what's already cooling toward forgetting
            lines.append(f"- {n.content}")
            if len(lines) >= _RECALL_K:
                break
        if not lines:
            return 0  # silence > noise: nothing worth surfacing
        ctx = (
            "Relevant memory from Coldframe (this user's local memory):\n"
            + "\n".join(lines)
            + f"\n(Full memory: run `{PKG} ui`.)"
        )
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
        print(json.dumps(out))
    except Exception:  # a hook failure must degrade to a silent no-op, never crash the session
        return 0
    return 0


def _hook_present(entries: object, cmd: str) -> bool:
    """True if our hook command is already wired in a settings SessionStart entry list."""
    if not isinstance(entries, list):
        return False
    for e in entries:
        hooks = e.get("hooks", []) if isinstance(e, dict) else []
        if isinstance(hooks, list):
            for h in hooks:
                if isinstance(h, dict) and cmd in str(h.get("command", "")):
                    return True
    return False


# (settings event, matcher, `hook` subcommand) — recall on SessionStart, capture on Stop (D26).
_HOOK_WIRING: tuple[tuple[str, str, str], ...] = (
    ("SessionStart", "startup|resume", "session-start"),
    ("Stop", "", "stop"),
)


def _cmd_hook_install(args: argparse.Namespace) -> int:
    path = _settings_path(user=not args.project)
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        print(f"{PKG}: couldn't read {path} — fix or remove it, then retry")
        return 1
    hooks = settings.setdefault("hooks", {})
    added: list[str] = []
    for event, matcher, sub in _HOOK_WIRING:
        cmd = f"{PKG} hook {sub}"
        entries = hooks.setdefault(event, [])
        if _hook_present(entries, cmd):  # idempotent
            continue
        entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]})
        added.append(event)
    if not added:
        print(f"already installed → {path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"installed {', '.join(added)} hook(s) → {path}")
    print("  recall on session start; auto-capture drains while Claude Code uses Coldframe's tools")
    return 0


def _cmd_hook_status(args: argparse.Namespace) -> int:
    for scope, user in (("user", True), ("project", False)):
        path = _settings_path(user=user)
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            settings = {}
        hooks = settings.get("hooks", {}) if isinstance(settings.get("hooks"), dict) else {}
        wired = [
            event
            for event, _m, sub in _HOOK_WIRING
            if _hook_present(hooks.get(event, []), f"{PKG} hook {sub}")
        ]
        state = ("✓ " + "+".join(wired)) if wired else "— not installed"
        print(f"{scope:8} {path}: {state}")
    print(f"install with: {PKG} hook install")
    return 0


def _cmd_hook_capture(args: argparse.Namespace) -> int:
    """Capture-commit hook (Stop / PreCompact): enqueue the session's transcript span for auto-
    capture and return immediately (no extraction here — that drains where an LLM is reachable).
    Fast + fail-silent: a hook must never block or crash the session (D26)."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tp = str(payload.get("transcript_path", ""))
        sid = str(payload.get("session_id", ""))
        if tp and sid:
            _memory(args).enqueue_capture(tp, sid)
    except Exception:  # fail-silent: never break the session on a capture hiccup
        return 0
    return 0


def _cmd_hook_help(args: argparse.Namespace) -> int:
    print(f"usage: {PKG} hook {{session-start|stop|install|status}}")
    return 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from cold_frame.mcp import main as mcp_main  # lazy: keeps heavy deps out of the CLI

    return mcp_main()


def _cmd_consolidate(args: argparse.Namespace) -> int:
    res = _memory(args).consolidate()  # manual forgetting trigger (SPEC §6); sync
    print(f"consolidate: archived {len(res.archived)}, merged {len(res.merged)}")
    return 0


def _cmd_reembed(args: argparse.Namespace) -> int:
    """Re-index notes under the currently-configured embedder (run after swapping embedders)."""
    res = _memory(args).reembed()
    if res.reembedded:
        print(f"reembed: re-indexed {res.reembedded} note(s) under {res.embedder_id}")
    else:
        print(f"reembed: nothing stale — all vectors current under {res.embedder_id}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mem = _memory(args)
    if args.events:  # portable append-only event-log dump (I17)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for line in mem.export_events():
                f.write(line + "\n")
                n += 1
        print(f"exported {n} events → {out}")
    else:  # complete consistent snapshot of the whole DB (I17)
        mem.snapshot(str(out))
        print(f"exported snapshot → {out}")
    return 0


def _db_is_busy(path: Path) -> bool:
    """True if another process holds ``path`` open (can't take an exclusive lock immediately)."""
    try:
        conn = sqlite3.connect(str(path), timeout=0.0)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("ROLLBACK")
            return False
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return True


def _cmd_import(args: argparse.Namespace) -> int:
    """Restore the memory DB from a snapshot (replaces the current DB; current is backed up)."""
    import shutil

    src = Path(args.path)
    dst = Path(_resolve_db(args))
    if not src.exists():
        print(f"import: source not found: {src}")
        return 1
    try:  # validate it is a cold-frame snapshot: valid SQLite + migrated + the notes table
        ro = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            version = int(ro.execute("PRAGMA user_version").fetchone()[0])
            has_notes = (
                ro.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
                ).fetchone()
                is not None
            )
        finally:
            ro.close()
    except sqlite3.Error:
        print(f"import: {src} is not a valid SQLite snapshot")
        return 1
    if version < 1 or not has_notes:
        print(f"import: {src} is not a cold-frame snapshot")
        return 1
    if dst.exists() and _db_is_busy(dst):  # I17: never replace a DB another process has open
        print(f"import: {dst} is in use — stop cold-frame (ui/mcp/worker) first, then retry")
        return 1
    try:
        if dst.exists():  # safety-backup the current DB, then drop its stale WAL/SHM
            shutil.copy2(dst, f"{dst}.pre-import.bak")
            for ext in ("-wal", "-shm"):
                stale = Path(f"{dst}{ext}")
                if stale.exists():
                    stale.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        bak = Path(f"{dst}.pre-import.bak")
        hint = f"; previous DB preserved at {bak}" if bak.exists() else ""
        print(f"import failed: {exc}{hint}")
        return 1
    note = (
        f" (previous DB backed up to {dst}.pre-import.bak)"
        if Path(f"{dst}.pre-import.bak").exists()
        else ""
    )
    print(f"imported {src} → {dst}{note}")
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    """Drain the durable jobs queue (consolidation + dead-letter recovery, I12)."""
    mem = _memory(args)
    if args.once:
        ran = mem.run_pending_jobs()
        print(f"worker: ran {ran} job(s)")
        return 0
    import time  # background poller: run pending jobs, sleep, repeat (Ctrl-C to stop)

    print(f"{PKG} worker: polling every {args.interval}s (Ctrl-C to stop)")
    try:
        while True:
            mem.run_pending_jobs()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    from cold_frame.ui.server import serve  # lazy import (stdlib-only server)

    port = args.port or branding.UI_PORT

    def _ready(resolved: int) -> None:
        print(f"{PKG} ui → {branding.ui_base_url(resolved)}  (Ctrl-C to stop)")

    try:
        serve(_memory(args), port=port, on_ready=_ready)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree (one subparser per SPEC §9 subcommand)."""
    parser = argparse.ArgumentParser(prog=PKG, description="local-first memory for LLM agents")
    parser.add_argument("--version", action="version", version=f"{PKG} {__version__}")
    parser.add_argument("--db", help="path to the memory.db (else $COLD_FRAME_DB or the default)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_add = sub.add_parser("add", help="add a fact or messages")
    p_add.add_argument("text", nargs="?", help="text to remember")
    p_add.add_argument("--raw", action="store_true", help="store verbatim, skip extraction")
    p_add.set_defaults(func=_cmd_add)

    p_search = sub.add_parser("search", help="search memory")
    p_search.add_argument("query", nargs="?", help="search query")
    p_search.add_argument("-k", type=int, default=10, help="number of hits")
    p_search.set_defaults(func=_cmd_search)

    p_list = sub.add_parser("list", help="list active notes")
    p_list.add_argument("--limit", type=int, default=50, help="max notes to list")
    p_list.set_defaults(func=_cmd_list)
    p_show = sub.add_parser("show", help="show one note by id")
    p_show.add_argument("id", nargs="?", help="note id")
    p_show.set_defaults(func=_cmd_show)
    sub.add_parser("stats", help="show store statistics").set_defaults(func=_cmd_stats)
    p_timeline = sub.add_parser("timeline", help="show a note's belief/version timeline")
    p_timeline.add_argument("id", nargs="?", help="note id (or unique prefix)")
    p_timeline.set_defaults(func=_cmd_timeline)
    p_path = sub.add_parser("path", help="show edge path between two notes")
    p_path.add_argument("src", help="source note id (or unique prefix)")
    p_path.add_argument("dst", help="destination note id (or unique prefix)")
    p_path.add_argument("--max-hops", type=int, default=4, help="max edges to traverse")
    p_path.set_defaults(func=_cmd_path)
    sub.add_parser("doctor", help="run install/DB/embedder/invariant checks").set_defaults(
        func=_cmd_doctor
    )
    sub.add_parser("consolidate", help="run forgetting/consolidation now").set_defaults(
        func=_cmd_consolidate
    )
    sub.add_parser("reembed", help="re-index vectors under the current embedder").set_defaults(
        func=_cmd_reembed
    )
    p_export = sub.add_parser("export", help="back up memory (snapshot, or --events NDJSON)")
    p_export.add_argument("path", help="output file path")
    p_export.add_argument("--events", action="store_true", help="dump the event log as NDJSON")
    p_export.set_defaults(func=_cmd_export)
    p_import = sub.add_parser("import", help="restore memory from a snapshot (replaces current)")
    p_import.add_argument("path", help="snapshot file to restore from")
    p_import.set_defaults(func=_cmd_import)
    p_worker = sub.add_parser("worker", help="drain the background jobs queue (maintenance)")
    p_worker.add_argument("--once", action="store_true", help="run one drain pass and exit")
    p_worker.add_argument("--interval", type=float, default=5.0, help="poll interval seconds")
    p_worker.set_defaults(func=_cmd_worker)
    p_ui = sub.add_parser("ui", help="launch the local web UI")
    p_ui.add_argument("--port", type=int, default=None, help="UI port (else 27182 + auto-fallback)")
    p_ui.set_defaults(func=_cmd_ui)
    sub.add_parser("mcp", help="run the MCP stdio server").set_defaults(func=_cmd_mcp)
    p_hook = sub.add_parser("hook", help="Claude Code auto-recall/capture hooks (D26)")
    p_hook.set_defaults(func=_cmd_hook_help)  # `hook` with no event → usage
    hook_sub = p_hook.add_subparsers(dest="hook_cmd", metavar="<event>")
    hook_sub.add_parser("session-start", help="emit recall context for a new session").set_defaults(
        func=_cmd_hook_session_start
    )
    hook_sub.add_parser(
        "stop", help="enqueue the session transcript for auto-capture"
    ).set_defaults(func=_cmd_hook_capture)
    p_hi = hook_sub.add_parser("install", help="wire the recall hook into Claude Code settings")
    p_hi.add_argument(
        "--project", action="store_true", help="install into ./.claude (else ~/.claude)"
    )
    p_hi.set_defaults(func=_cmd_hook_install)
    hook_sub.add_parser("status", help="show whether the recall hook is installed").set_defaults(
        func=_cmd_hook_status
    )
    sub.add_parser("setup", help="first-run setup").set_defaults(func=_cmd_setup)
    p_purge = sub.add_parser("purge", help="hard-scrub a secret/PII note (irreversible)")
    p_purge.add_argument("id", nargs="?", help="note id (or unique prefix)")
    p_purge.add_argument("--force", action="store_true", help="confirm the irreversible scrub")
    p_purge.add_argument("--cascade", action="store_true", help="also purge derived summaries")
    p_purge.set_defaults(func=_cmd_purge)

    for name in _SUBCOMMANDS:  # default any not-yet-wired subcommand to the stub handler
        if not callable(sub.choices[name].get_default("func")):
            sub.choices[name].set_defaults(func=_not_implemented)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    func = args.func
    try:
        result: int = func(args)
        return result
    finally:  # release DB connections (so e.g. `import`'s file-replace isn't held open)
        for mem in _OPENED:
            mem.close()
        _OPENED.clear()


if __name__ == "__main__":
    raise SystemExit(main())
