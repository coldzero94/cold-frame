"""MCP server tests (P1 unit 10): async seam (I4), tool logic, error mapping, install guard."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest
from cold_frame import mcp as mcpmod
from cold_frame.api import Memory
from cold_frame.exceptions import NoteNotFound

_HAS_SDK = importlib.util.find_spec("mcp") is not None


def test_async_def_only_in_mcp_module() -> None:
    """I4: `async def` appears ONLY in cold_frame/mcp.py (sync core + one async seam)."""
    pkg_root = Path(mcpmod.__file__).resolve().parent
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        if py.name == "mcp.py":
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("async def "):
                offenders.append(str(py.relative_to(pkg_root)))
                break
    assert offenders == [], f"async def outside mcp.py: {offenders}"


def test_tool_handlers_are_async() -> None:
    assert inspect.iscoroutinefunction(mcpmod.search_memory)
    assert inspect.iscoroutinefunction(mcpmod.add_memory)
    for tool in (mcpmod.create_fact, mcpmod.update_fact, mcpmod.supersede, mcpmod.forget):
        assert inspect.iscoroutinefunction(tool)


def test_self_edit_impl_routes_through_writecore(db_path: str) -> None:
    mem = Memory(db_path)
    created = mcpmod._self_edit_impl(mem, "create_fact", {"text": "I work at Vessl"})
    fid = created["added"][0]
    updated = mcpmod._self_edit_impl(mem, "update_fact", {"id": fid, "text": "I work at Anthropic"})
    assert updated["archived"] == fid and updated["new"]
    assert mem.get(fid).status == "archived"  # superseded via the same WriteCore path (I15)
    assert mem.search("Anthropic").hits[0].note.id == updated["new"]


def test_search_impl_empty_is_success(db_path: str) -> None:
    mem = Memory(db_path)
    assert mcpmod._search_impl(mem, "nothing matches zzz") == {"hits": [], "used": 0}


def test_add_then_search_impl_roundtrip(db_path: str) -> None:
    mem = Memory(db_path)
    added = mcpmod._add_impl(mem, "I prefer dark roast coffee")
    assert added["added"] and added["added"][0]["content"] == "I prefer dark roast coffee"
    assert added["added"][0]["deeplink"].endswith(f"/fact/{added['added'][0]['id']}")
    # agent-facing parity with the CLI: the admission reports are always present (content-free)
    assert added["blocked"] == [] and added["redacted"] == []

    res = mcpmod._search_impl(mem, "coffee")
    assert res["hits"] and "dark roast" in res["hits"][0]["content"]


def test_error_response_maps_not_found() -> None:
    resp = mcpmod._error_response(NoteNotFound("missing"))
    assert resp["error"]["code"] == "not_found"


@pytest.mark.skipif(not _HAS_SDK, reason="needs the [mcp] extra (anyio)")
def test_mcp_self_edit_error_maps_to_stable_code(db_path: str) -> None:
    import anyio

    mcpmod._MEMORY = Memory(db_path)
    try:
        # NoteNotFound (update/supersede/forget on a ghost id) → not_found through the async seam
        for handler in (mcpmod.update_fact, mcpmod.supersede):
            resp = anyio.run(handler, "ghost-id", "new text")
            assert resp["error"]["code"] == "not_found"
        forget_resp = anyio.run(mcpmod.forget, "ghost-id")
        assert forget_resp["error"]["code"] == "not_found"
    finally:
        mcpmod._MEMORY = None


@pytest.mark.skipif(_HAS_SDK, reason="mcp SDK installed → import-guard path not exercised")
def test_main_reports_install_hint_without_sdk(capsys: pytest.CaptureFixture[str]) -> None:
    rc = mcpmod.main()
    assert rc == 2
    # the hint goes to STDERR (a stdio MCP server reserves stdout for the JSON-RPC protocol)
    assert "[mcp]" in capsys.readouterr().err


def test_require_memory_raises_when_uninitialized() -> None:
    from cold_frame.exceptions import StoreError

    prev = mcpmod._MEMORY
    mcpmod._MEMORY = None
    try:
        with pytest.raises(StoreError):
            mcpmod._require_memory()
    finally:
        mcpmod._MEMORY = prev


def test_drain_captures_swallows_errors(db_path: str) -> None:
    # a capture-drain hiccup must NEVER fail the agent's tool call (best-effort, content-free I16)
    mem = Memory(db_path)

    def _boom(**_: object) -> int:
        raise RuntimeError("drain boom")

    mem.run_pending_jobs = _boom  # type: ignore[method-assign]
    mcpmod._drain_captures(mem)  # must not raise


@pytest.mark.skipif(not _HAS_SDK, reason="needs the [mcp] extra (anyio)")
def test_async_tool_wrappers_happy_path(db_path: str) -> None:
    # the agent-facing async seam end to end: add → search → self-edit, through anyio.to_thread.
    import anyio

    mcpmod._MEMORY = Memory(db_path)
    try:
        added = anyio.run(mcpmod.add_memory, "I prefer dark roast coffee")
        assert added["added"] and added["added"][0]["content"] == "I prefer dark roast coffee"

        found = anyio.run(mcpmod.search_memory, "coffee")
        assert found["hits"] and "dark roast" in found["hits"][0]["content"]

        created = anyio.run(mcpmod.create_fact, "I use vim")
        fid = created["added"][0]
        updated = anyio.run(mcpmod.update_fact, fid, "I use neovim")
        assert updated["archived"] == fid and updated["new"]
        forgotten = anyio.run(mcpmod.forget, updated["new"])
        assert forgotten["status"] == "archived"
    finally:
        mcpmod._MEMORY = None


@pytest.mark.skipif(not _HAS_SDK, reason="needs the [mcp] extra (FastMCP)")
def test_build_server_binds_memory_and_registers_tools(db_path: str) -> None:
    # build_server with an explicit memory: binds it + registers the 6-tool set (no disk/env probe)
    mem = Memory(db_path)
    prev = mcpmod._MEMORY
    try:
        server = mcpmod.build_server(memory=mem)
        assert server is not None
        assert mcpmod._MEMORY is mem  # bound to the provided memory, not an env-constructed one
    finally:
        mcpmod._MEMORY = prev
