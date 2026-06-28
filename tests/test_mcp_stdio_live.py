"""LIVE: drive the real MCP server over actual stdio (initialize → tools/list → tools/call).

This is the one thing the unit tests can't fake — it spawns `cold-frame mcp` as a subprocess and
speaks the MCP protocol to it with the SDK client, proving the transport + tool wiring actually work
end-to-end (the readiness audit flagged that no such test existed). Opt-in (spawns a process, needs
the [mcp] extra): run with `COLD_FRAME_LIVE=1 uv run pytest -m live`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.mark.live
def test_mcp_server_roundtrip_over_real_stdio() -> None:
    if not os.environ.get("COLD_FRAME_LIVE"):
        pytest.skip("set COLD_FRAME_LIVE=1 to run the live stdio MCP test")
    pytest.importorskip("mcp")
    import anyio
    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    cold_frame = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "cold-frame"
    if not cold_frame.exists():
        pytest.skip("dev-env cold-frame console script not found")
    db = str(Path(tempfile.mkdtemp()) / "m.db")
    proj = tempfile.mkdtemp()

    async def _drive() -> None:
        params = StdioServerParameters(
            command=str(cold_frame),
            args=["mcp"],
            env={**os.environ, "COLD_FRAME_DB": db, "PROJECT_ROOT": proj},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()  # the real handshake
            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"add_memory", "search_memory"} <= tools
            await session.call_tool("add_memory", {"text": "I deploy with ship.sh in production"})
            res = await session.call_tool("search_memory", {"query": "how do I deploy"})
            text = res.content[0].text if res.content else ""
            assert "ship.sh" in text  # the fact added over stdio is searchable over stdio

    anyio.run(_drive)
