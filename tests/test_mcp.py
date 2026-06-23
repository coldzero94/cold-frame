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

    res = mcpmod._search_impl(mem, "coffee")
    assert res["hits"] and "dark roast" in res["hits"][0]["content"]


def test_error_response_maps_not_found() -> None:
    resp = mcpmod._error_response(NoteNotFound("missing"))
    assert resp["error"]["code"] == "not_found"


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
    assert "[mcp]" in capsys.readouterr().out
