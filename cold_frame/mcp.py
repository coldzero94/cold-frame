"""MCP stdio server (SPEC §8) — the ONE async seam (I4).

Leaf stub. This is the only module allowed ``async def``: each tool handler wraps a
sync ``Memory`` call in ``anyio.to_thread.run_sync``. The ``mcp`` SDK is import-guarded
(behind the ``[server]``/MCP extra, I9) so importing this module never pulls heavy deps
into core. ``main()`` is a stub until P1 wires up ``FastMCP``.
"""

from __future__ import annotations

from cold_frame.branding import MCP_ID


def main() -> int:
    """Run the ``cold-frame`` MCP stdio server (stub). Returns a process exit code."""
    # P1: lazily import the `mcp` SDK here (import-guarded, I9), construct one Memory,
    # register search_memory/add_memory/summarize/correct_memory + resources, serve stdio.
    print(f"{MCP_ID}: MCP server not implemented")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
