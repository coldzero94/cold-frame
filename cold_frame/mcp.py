"""MCP stdio server (SPEC §8) — the ONE async seam (I4).

This is the only module with ``async def``: each tool handler wraps a SYNC ``Memory``
call in ``anyio.to_thread.run_sync`` (no sync/async logic duplication — the logic lives
in the sync ``_*_impl`` helpers). The ``mcp`` SDK and ``anyio`` are imported LAZILY
(behind the ``[mcp]`` extra, I9) so importing this module never pulls heavy deps into
core; ``main()`` reports a clean install hint when the SDK is absent.
"""

from __future__ import annotations

from typing import Any

from cold_frame.api import Memory
from cold_frame.branding import MCP_ID, PKG, fact_deeplink
from cold_frame.exceptions import ColdFrameError, StoreError, mcp_code_for
from cold_frame.llm.sampling import SamplingLLM
from cold_frame.observability import get_logger

_log = get_logger(__name__)

_INSTALL_HINT = (
    f"{MCP_ID}: the MCP server needs an optional dependency — "
    f"install it with `pip install {PKG}[mcp]` (or `uv sync --extra mcp`)."
)

# One Memory per server process, set by build_server() at serve time.
_MEMORY: Memory | None = None
_SERVER: Any = None  # the FastMCP server (for get_context()); set in build_server()


def _require_memory() -> Memory:
    if _MEMORY is None:
        raise StoreError("MCP server memory is not initialized")
    return _MEMORY


def _host_sample(system: str, user: str) -> str:
    """Sync sampler bridging cold-frame's LLM seam to HOST MCP sampling (the parasitic LLM).

    cold-frame's internal judges (dedup/conflict) ride on the host agent's model — no own key.
    Runs inside a worker thread (the sync core, I4); ``anyio.from_thread.run`` bridges the one
    completion back to the event loop. ANY failure (no active request, host doesn't support
    sampling, error) returns ``""`` → ``SamplingLLM`` yields ``parsed=None`` → the deterministic
    engine decides, exactly as offline.
    """
    if _SERVER is None:
        return ""
    import anyio

    try:
        ctx = _SERVER.get_context()  # current request's Context (anyio copies it into this thread)
    except Exception:
        return ""

    async def _ask() -> str:
        from mcp.types import SamplingMessage, TextContent

        result = await ctx.session.create_message(
            messages=[SamplingMessage(role="user", content=TextContent(type="text", text=user))],
            system_prompt=system or None,
            max_tokens=1024,
            temperature=0.0,
        )
        content = result.content
        return content.text if getattr(content, "type", "") == "text" else ""

    try:
        return anyio.from_thread.run(_ask)
    except Exception as exc:  # host declined / no sampling capability / bridge error → degrade
        _log.warning("host_sample_failed", extra={"exc_type": type(exc).__name__})
        return ""


# ── sync tool logic (the single implementation; fully testable offline) ───────
def _search_impl(mem: Memory, query: str, k: int = 10) -> dict[str, Any]:
    res = mem.search(query, k=k)
    return {
        "hits": [
            {
                "id": h.note.id,
                "content": h.note.content,
                "score": h.score,
                "deeplink": fact_deeplink(h.note.id),
            }
            for h in res.hits
        ],
        "used": res.used_tokens or 0,
    }


def _add_impl(mem: Memory, text: str) -> dict[str, Any]:
    # raw=True: the agent's text IS the fact (naive). Smart dedup/conflict still rides on the
    # host via sampling inside commit; if extraction were LLM-driven, a sampling miss would drop
    # the fact entirely — naive keeps it, and the judges degrade safely.
    res = mem.add(text, raw=True)
    return {
        "added": [
            {"id": n.id, "content": n.content, "deeplink": fact_deeplink(n.id)} for n in res.added
        ],
        "held": [n.id for n in res.held],
        "blocked": [b.reason for b in res.blocked],
    }


def _self_edit_impl(mem: Memory, name: str, args: dict[str, object]) -> dict[str, Any]:
    """Single sync impl for every self-edit tool — routes through the one WriteCore (I15)."""
    return mem.apply_tool(name, args)


def _error_response(exc: ColdFrameError) -> dict[str, Any]:
    """Map an internal error to a stable MCP error code (exceptions.mcp_code_for)."""
    return {"error": {"code": mcp_code_for(exc), "message": str(exc)}}


# ── async tool handlers (the ONLY async in the codebase, I4) ──────────────────
async def search_memory(query: str, k: int = 10) -> dict[str, Any]:
    """MCP tool: search memory; returns {hits:[{id,content,score,deeplink}], used}."""
    import anyio

    mem = _require_memory()
    try:
        result: dict[str, Any] = await anyio.to_thread.run_sync(lambda: _search_impl(mem, query, k))
        return result
    except ColdFrameError as exc:
        return _error_response(exc)


async def add_memory(text: str) -> dict[str, Any]:
    """MCP tool: add a fact/messages; returns {added, held, blocked}."""
    import anyio

    mem = _require_memory()
    try:
        result: dict[str, Any] = await anyio.to_thread.run_sync(lambda: _add_impl(mem, text))
        return result
    except ColdFrameError as exc:
        return _error_response(exc)


async def _run_self_edit(name: str, args: dict[str, object]) -> dict[str, Any]:
    import anyio

    mem = _require_memory()
    try:
        result: dict[str, Any] = await anyio.to_thread.run_sync(
            lambda: _self_edit_impl(mem, name, args)
        )
        return result
    except ColdFrameError as exc:
        return _error_response(exc)


async def create_fact(text: str, memory_type: str = "semantic") -> dict[str, Any]:
    """MCP self-edit tool: assert a new fact (dedup; conflict when an LLM is set)."""
    return await _run_self_edit("create_fact", {"text": text, "memory_type": memory_type})


async def update_fact(id: str, text: str) -> dict[str, Any]:
    """MCP self-edit tool: replace the fact at `id` with `text`; returns {archived, new}."""
    return await _run_self_edit("update_fact", {"id": id, "text": text})


async def supersede(id: str, text: str) -> dict[str, Any]:
    """MCP self-edit tool: supersede the fact at `id` with a new fact; returns {archived,new}."""
    return await _run_self_edit("supersede", {"id": id, "text": text})


async def forget(id: str) -> dict[str, Any]:
    """MCP self-edit tool: archive the fact at `id` (revivable); returns {archived,status}."""
    return await _run_self_edit("forget", {"id": id})


# ── server wiring (lazy SDK import, I9) ───────────────────────────────────────
def build_server(memory: Memory | None = None) -> Any:  # noqa: ANN401 - FastMCP type optional
    """Construct the FastMCP stdio server with the two tools registered.

    Raises ``ColdFrameError`` (install hint) if the ``[mcp]`` extra is not installed —
    BEFORE touching disk, so a missing SDK never creates a DB.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ColdFrameError(_INSTALL_HINT) from exc

    global _MEMORY, _SERVER
    server = FastMCP(MCP_ID)
    _SERVER = server
    # default: ride on the host's model via MCP sampling (no own key/endpoint) — degrade-safe
    _MEMORY = memory if memory is not None else Memory(llm=SamplingLLM(_host_sample))
    server.tool()(search_memory)
    server.tool()(add_memory)
    for tool in (create_fact, update_fact, supersede, forget):  # self-edit tools (one WriteCore)
        server.tool()(tool)
    return server


def main() -> int:
    """Run the ``cold-frame`` MCP stdio server. Returns a process exit code."""
    try:
        server = build_server()
    except ColdFrameError as exc:
        print(str(exc))
        return 2
    server.run()  # serve over stdio (blocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
