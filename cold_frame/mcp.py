"""MCP stdio server (SPEC §8) — the ONE async seam (I4).

This is the only module with ``async def``: each tool handler wraps a SYNC ``Memory``
call in ``anyio.to_thread.run_sync`` (no sync/async logic duplication — the logic lives
in the sync ``_*_impl`` helpers). The ``mcp`` SDK and ``anyio`` are imported LAZILY
(behind the ``[mcp]`` extra, I9) so importing this module never pulls heavy deps into
core; ``main()`` reports a clean install hint when the SDK is absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cold_frame.api import Memory
from cold_frame.branding import MCP_ID, PKG, fact_deeplink
from cold_frame.exceptions import ColdFrameError, StoreError, mcp_code_for
from cold_frame.integrations.claude_code import GLOBAL_KEY, project_key
from cold_frame.llm.sampling import SamplingLLM
from cold_frame.models import Scope
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
# Auto-capture (D26) drains here: the hook only ENQUEUES; the host model is reachable ONLY inside a
# live MCP request (_host_sample returns "" otherwise), so capture-extraction piggybacks on a tool
# call. Bounded so it never noticeably delays the agent's actual request.
_CAPTURE_DRAIN_MAX = 2


def _drain_captures(mem: Memory) -> None:
    """Drain pending capture jobs while this request is live → extraction uses the host model via
    sampling (D26). Best-effort: a drain hiccup must never fail the agent's tool call."""
    try:
        mem.run_pending_jobs(max_jobs=_CAPTURE_DRAIN_MAX)
    except Exception as exc:  # content-free (I16); the durable queue retries
        _log.warning("capture_drain_failed", extra={"exc_type": type(exc).__name__})


def _scope_tiers(mem: Memory) -> list[Scope]:
    """This project's scope + the global tier (D26) — dedup'd when the server runs outside a repo
    (project_key → GLOBAL_KEY). Mirrors the SessionStart recall so the tool path can't leak across
    projects (the default Scope(agent_id=None) would match EVERY project's tier)."""
    project = mem._default_scope
    scopes = [project]
    if project.agent_id != GLOBAL_KEY:
        scopes.append(Scope(agent_id=GLOBAL_KEY))
    return scopes


def _search_impl(mem: Memory, query: str, k: int = 10) -> dict[str, Any]:
    # search THIS project + global, never the default all-tiers scope (cross-project leak, D26).
    best: dict[str, Any] = {}
    used = 0
    for scope in _scope_tiers(mem):
        res = mem.search(query, k=k, scope=scope)
        used += res.used_tokens or 0
        for h in res.hits:
            if h.note.id not in best or h.score > best[h.note.id].score:
                best[h.note.id] = h
    top = sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]
    out: dict[str, Any] = {
        "hits": [
            {
                "id": h.note.id,
                "content": h.note.content,
                "score": h.score,
                "deeplink": fact_deeplink(h.note.id),
            }
            for h in top
        ],
        "used": used,
    }
    _drain_captures(mem)  # piggyback auto-capture extraction on this live request
    return out


def _add_impl(mem: Memory, text: str) -> dict[str, Any]:
    # raw=True: the agent's text IS the fact (naive). Smart dedup/conflict still rides on the
    # host via sampling inside commit; if extraction were LLM-driven, a sampling miss would drop
    # the fact entirely — naive keeps it, and the judges degrade safely. Tier it like auto-capture
    # (global vs this project) so an agent-asserted fact is recalled in the right scope (D26).
    is_global = mem._classify_tiers([text])[0]
    scope = Scope(agent_id=GLOBAL_KEY) if is_global else mem._default_scope
    res = mem.add(text, raw=True, scope=scope)
    out = {
        "added": [
            {"id": n.id, "content": n.content, "deeplink": fact_deeplink(n.id)} for n in res.added
        ],
        "held": [n.id for n in res.held],
        "blocked": [b.reason for b in res.blocked],
    }
    _drain_captures(mem)  # also drain pending captures on an explicit add
    return out


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
    # Scope the server to its project tier (D26). Claude Code does NOT reliably pass the project cwd
    # to an MCP subprocess (#42687 — os.getcwd() may be a cache dir), so prefer the PROJECT_ROOT env
    # the user sets at `claude mcp add --env PROJECT_ROOT="$PWD"`; fall back to cwd. (SamplingLLM is
    # kept for the dedup/conflict judges + future clients; Claude Code can't service sampling today,
    # so it degrades to the deterministic engine — the agent-push directive does the real capture.)
    if memory is not None:
        _MEMORY = memory
    else:
        root = os.environ.get("PROJECT_ROOT") or str(Path.cwd())
        scope = Scope(agent_id=project_key(root))
        _MEMORY = Memory(llm=SamplingLLM(_host_sample), default_scope=scope)
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
