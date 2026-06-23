"""``cold-frame`` CLI (SPEC §9). Entry point: ``cold-frame = "cold_frame.cli:main"``.

P1 wires the offline path: ``add`` → ``search`` recalls the just-added fact with zero
keys/network (I5), plus ``doctor`` (invariant check) and ``mcp`` (dispatch to the stdio
server). Other subcommands remain stubs until their phase. The DB location resolves from
``--db`` → ``$COLD_FRAME_DB`` → ``branding.DB_PATH`` (no literal path strings, branding rule).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from cold_frame import __version__, branding
from cold_frame.api import Memory
from cold_frame.branding import PKG

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
    "ui",
    "mcp",
    "setup",
)


def _resolve_db(args: argparse.Namespace) -> str:
    return args.db or os.environ.get("COLD_FRAME_DB") or str(branding.DB_PATH)


def _memory(args: argparse.Namespace) -> Memory:
    return Memory(_resolve_db(args))  # offline default: HashEmbedder + llm=None


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"{PKG}: '{args.command}' not implemented")
    return 1


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


def _cmd_doctor(args: argparse.Namespace) -> int:
    h = _memory(args).health()
    print(f"db: {h['db_path']}")
    print(f"notes={h['notes']} fts={h['fts']} vec={h['vec']}  (match={h['counts_match']})")
    print(f"integrity_check={h['integrity']}  fts_integrity={h['fts_integrity']}")
    print(f"embedder={h['embedder_id']} dim={h['dim']}  stale_vectors={h['stale_vectors']}")
    print(f"ui_port={branding.UI_PORT}")
    ok = (
        bool(h["counts_match"])
        and h["integrity"] == "ok"
        and h["fts_integrity"] == "ok"
        and h["stale_vectors"] == 0
    )
    print("ok" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from cold_frame.mcp import main as mcp_main  # lazy: keeps heavy deps out of the CLI

    return mcp_main()


def _cmd_consolidate(args: argparse.Namespace) -> int:
    res = _memory(args).consolidate()  # manual forgetting trigger (SPEC §6); sync
    print(f"consolidate: archived {len(res.archived)}, merged {len(res.merged)}")
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

    sub.add_parser("list", help="list active notes")
    p_show = sub.add_parser("show", help="show one note by id")
    p_show.add_argument("id", nargs="?", help="note id")
    sub.add_parser("stats", help="show store statistics")
    sub.add_parser("timeline", help="show the belief/consolidation timeline")
    sub.add_parser("path", help="show edge path between notes")
    sub.add_parser("doctor", help="run install/DB/embedder/invariant checks").set_defaults(
        func=_cmd_doctor
    )
    sub.add_parser("consolidate", help="run forgetting/consolidation now").set_defaults(
        func=_cmd_consolidate
    )
    p_worker = sub.add_parser("worker", help="drain the background jobs queue (maintenance)")
    p_worker.add_argument("--once", action="store_true", help="run one drain pass and exit")
    p_worker.add_argument("--interval", type=float, default=5.0, help="poll interval seconds")
    p_worker.set_defaults(func=_cmd_worker)
    p_ui = sub.add_parser("ui", help="launch the local web UI")
    p_ui.add_argument("--port", type=int, default=None, help="UI port (else 27182 + auto-fallback)")
    p_ui.set_defaults(func=_cmd_ui)
    sub.add_parser("mcp", help="run the MCP stdio server").set_defaults(func=_cmd_mcp)
    sub.add_parser("setup", help="first-run setup")

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
    result: int = func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
