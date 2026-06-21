"""``cold-frame`` CLI — argparse skeleton (SPEC §9).

Leaf stub: subcommand wiring is in place, but every handler prints "not implemented"
and returns a non-zero exit code. P1+ fills the handlers in (offline ``add``→``search``
is the P1 gate). Entry point: ``cold-frame = "cold_frame.cli:main"`` (pyproject).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cold_frame import __version__
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
    "ui",
    "mcp",
    "setup",
)


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"{PKG}: '{args.command}' not implemented")
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree (one subparser per SPEC §9 subcommand)."""
    parser = argparse.ArgumentParser(prog=PKG, description="local-first memory for LLM agents")
    parser.add_argument("--version", action="version", version=f"{PKG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_add = sub.add_parser("add", help="add a fact or messages")
    p_add.add_argument("text", nargs="?", help="text to remember")
    p_add.add_argument("--raw", action="store_true", help="store verbatim, skip extraction")

    p_search = sub.add_parser("search", help="search memory")
    p_search.add_argument("query", nargs="?", help="search query")
    p_search.add_argument("-k", type=int, default=10, help="number of hits")

    sub.add_parser("list", help="list active notes")

    p_show = sub.add_parser("show", help="show one note by id")
    p_show.add_argument("id", nargs="?", help="note id")

    sub.add_parser("stats", help="show store statistics")
    sub.add_parser("timeline", help="show the belief/consolidation timeline")
    sub.add_parser("path", help="show edge path between notes")
    sub.add_parser("doctor", help="run install/DB/embedder/invariant checks")
    sub.add_parser("ui", help="launch the local web UI")
    sub.add_parser("mcp", help="run the MCP stdio server")
    sub.add_parser("setup", help="first-run setup")

    for name in _SUBCOMMANDS:
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
