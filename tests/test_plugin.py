"""The Claude Code plugin manifest stays consistent with the CLI (no silent drift)."""

from __future__ import annotations

import json
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parent.parent / "packaging" / "plugin"


def test_plugin_manifest_is_valid() -> None:
    m = json.loads((_PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] and m["version"] and m["description"]
    assert m["hooks"] == "./hooks/hooks.json" and m["mcpServers"] == "./.mcp.json"


def test_plugin_registers_the_mcp_server() -> None:
    mcp = json.loads((_PLUGIN / ".mcp.json").read_text())["mcpServers"]
    assert mcp["cold-frame"]["command"] == "cold-frame"
    assert mcp["cold-frame"]["args"] == ["mcp"]
    assert "PROJECT_ROOT" in mcp["cold-frame"]["env"]  # per-project scoping (cwd isn't reliable)


def test_plugin_hooks_reference_real_cli_subcommands() -> None:
    # if a `hook` subcommand is renamed, this catches the now-broken plugin wiring.
    from cold_frame.cli import _HOOK_WIRING

    valid = {sub for _ev, _m, sub in _HOOK_WIRING}
    hooks = json.loads((_PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    cmds = {hk["command"] for ev in hooks.values() for e in ev for hk in e["hooks"]}
    assert cmds, "no hook commands in the plugin"
    for c in cmds:
        assert c.startswith("cold-frame hook "), c
        assert c.rsplit(" ", 1)[1] in valid, f"{c!r} not a known hook subcommand {valid}"


def test_plugin_capture_skill_present() -> None:
    skill = (_PLUGIN / "skills" / "remember-facts" / "SKILL.md").read_text()
    assert skill.startswith("---")  # frontmatter
    assert "description:" in skill and "add_memory" in skill  # the agent-push capture instruction
