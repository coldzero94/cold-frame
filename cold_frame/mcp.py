"""MCP stdio server (SPEC §8) — the ONE async seam (I4).

This is the only module with ``async def``: each tool handler wraps a SYNC ``Memory``
call in ``anyio.to_thread.run_sync`` (no sync/async logic duplication — the logic lives
in the sync ``_*_impl`` helpers). The ``mcp`` SDK and ``anyio`` are imported LAZILY
(behind the ``[mcp]`` extra, I9) so importing this module never pulls heavy deps into
core; ``main()`` reports a clean install hint when the SDK is absent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from cold_frame.api import Memory
from cold_frame.branding import MCP_ID, PKG, REPO_URL, fact_deeplink
from cold_frame.exceptions import ColdFrameError, StoreError, mcp_code_for
from cold_frame.integrations.claude_code import GLOBAL_KEY, project_key
from cold_frame.llm import resolve_embedder, resolve_llm
from cold_frame.models import Scope
from cold_frame.observability import get_logger

_log = get_logger(__name__)

_INSTALL_HINT = (
    f"{MCP_ID}: the MCP server needs an optional dependency — install it with "
    f"`uv sync --extra mcp` (or `pip install '{PKG}[mcp] @ git+{REPO_URL}'`; not on PyPI)."
)

# One Memory per server process, set by build_server() at serve time.
_MEMORY: Memory | None = None


def _require_memory() -> Memory:
    if _MEMORY is None:
        raise StoreError("MCP server memory is not initialized")
    return _MEMORY


# ── sync tool logic (the single implementation; fully testable offline) ───────
# Auto-capture (D26): the hook only ENQUEUES; this drains the durable queue as a deterministic
# coverage backstop (naive — the MCP server has no model; Claude Code can't service MCP sampling, so
# we don't pretend to). High-quality extraction is the agent-push skill (the agent calls add_memory)
# or `cold-frame worker` with ClaudeCliLLM/local. Bounded so it never noticeably delays the request.
_CAPTURE_DRAIN_MAX = 2


def _drain_captures(mem: Memory) -> None:
    """Drain pending capture jobs (the naive coverage backstop) while this request is live.
    Best-effort: a drain hiccup must never fail the agent's tool call."""
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
        # reinforce=False per tier: a note surfaced in one tier but dropped from the merged top-k
        # must NOT get a reinforcement bump. We reinforce the actually-returned set once, below.
        res = mem.search(query, k=k, scope=scope, reinforce=False)
        used += res.used_tokens or 0
        for h in res.hits:
            if h.note.id not in best or h.score > best[h.note.id].score:
                best[h.note.id] = h
    top = sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]
    if top:  # reinforcement tracks what was actually surfaced (one bump per returned note)
        try:  # best-effort (mirror the read pipeline): a reinforce failure must NOT fail recall
            mem._store.reinforce([h.note.id for h in top], now=mem._clock.now())
        except StoreError:
            _log.warning("mcp_search_reinforce_failed")
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
    # raw=True: the agent's text IS the fact — the agent already extracted it (agent-push), so no
    # re-extraction. dedup/conflict are deterministic here (the server has no model). Tier it like
    # auto-capture (global vs this project) so the fact is recalled in the right scope.
    is_global = mem._classify_tiers([text])[0]
    scope = Scope(agent_id=GLOBAL_KEY) if is_global else mem._default_scope
    res = mem.add(text, raw=True, scope=scope)
    out = {
        "added": [
            {"id": n.id, "content": n.content, "deeplink": fact_deeplink(n.id)} for n in res.added
        ],
        "held": [n.id for n in res.held],
        "blocked": [b.reason for b in res.blocked],
        # content-free PII report (I16) — parity with the CLI; empty unless redaction is configured
        "redacted": [{"category": r.category, "count": r.count} for r in res.redacted],
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
    """MCP tool: add a fact/messages; returns {added, held, blocked, redacted}."""
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
    """Construct the FastMCP stdio server with its tools registered (search_memory + add_memory +
    the create_fact/update_fact/supersede/forget self-edit set).

    Raises ``ColdFrameError`` (install hint) if the ``[mcp]`` extra is not installed —
    BEFORE touching disk, so a missing SDK never creates a DB.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ColdFrameError(_INSTALL_HINT) from exc

    global _MEMORY
    server = FastMCP(MCP_ID)
    # Scope the server to its project tier (D26). Claude Code does NOT reliably pass the project cwd
    # to an MCP subprocess (#42687 — os.getcwd() may be a cache dir), so prefer the PROJECT_ROOT env
    # the user sets at `claude mcp add --env PROJECT_ROOT="$PWD"`; fall back to cwd. llm=None: the
    # server is deterministic inline (no MCP sampling — Claude Code can't service it); quality
    # extraction is the agent-push skill or `cold-frame worker` (ClaudeCliLLM/local).
    if memory is not None:
        _MEMORY = memory
    else:
        root = os.environ.get("PROJECT_ROOT") or str(Path.cwd())
        # $COLD_FRAME_EMBEDDER selects the recall model (unset/"hash" = offline default; "local" =
        # the [local-llm] semantic embedder). $COLD_FRAME_LLM="claude" turns on the dedup/conflict
        # judges (auto conflict detection) via the session-auth Claude CLI; unset = deterministic.
        _MEMORY = Memory(
            embedder=resolve_embedder(os.environ.get("COLD_FRAME_EMBEDDER")),
            llm=resolve_llm(os.environ.get("COLD_FRAME_LLM")),
            default_scope=Scope(agent_id=project_key(root)),
        )
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
        # a stdio MCP server owns STDOUT for JSON-RPC — a human-readable startup error goes to
        # STDERR (else it corrupts the protocol stream a client may already be reading).
        print(str(exc), file=sys.stderr)
        return 2
    server.run()  # serve over stdio (blocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
