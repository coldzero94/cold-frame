"""Plugin onboarding guard — the shipped Claude Code plugin manifests must match the CLI reality.

test_hooks.py drives ``cold-frame hook <sub>`` directly (the loop works). But the PLUGIN wires those
commands through static manifests (hooks.json / .mcp.json / plugin.json); if one drifts from the CLI
— a renamed subcommand, a changed matcher, a wrong server command — the unit tests stay green while
``claude plugin install coldframe`` silently wires a command that does nothing. These pin the
manifests to the CLI so that class of onboarding breakage fails CI instead.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from cold_frame import branding
from cold_frame.cli import _HOOK_WIRING, main

_PLUGIN = Path(__file__).resolve().parent.parent / "packaging" / "plugin"


def _load(rel: str) -> dict:
    return json.loads((_PLUGIN / rel).read_text(encoding="utf-8"))


def test_hooks_json_matches_the_cli_wiring_exactly() -> None:
    # reconstruct (event, matcher, command) from the shipped manifest ...
    manifest: set[tuple[str, str, str]] = set()
    for event, entries in _load("hooks/hooks.json")["hooks"].items():
        for entry in entries:
            matcher = entry.get("matcher", "")
            for h in entry["hooks"]:
                manifest.add((event, matcher, h["command"]))
    # ... and it must equal the CLI source of truth (nothing missing, nothing extra)
    expected = {
        (event, matcher, f"{branding.PKG} hook {sub}") for event, matcher, sub in _HOOK_WIRING
    }
    assert manifest == expected


def test_every_plugin_hook_command_is_a_runnable_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the exact command each hook ships must actually run (empty stdin + empty DB → silent exit 0),
    # so the manifest can't wire a command the CLI would reject.
    db = str(tmp_path / "m.db")
    for entry_list in _load("hooks/hooks.json")["hooks"].values():
        for entry in entry_list:
            for h in entry["hooks"]:
                parts = h["command"].split()
                assert parts[0] == branding.PKG and parts[1] == "hook"  # `cold-frame hook <sub>`
                monkeypatch.setattr("sys.stdin", io.StringIO(""))  # no payload → hook no-ops
                assert main(["--db", db, *parts[1:]]) == 0


def test_mcp_json_wires_the_real_server() -> None:
    servers = _load(".mcp.json")["mcpServers"]
    assert branding.MCP_ID in servers  # the server id the CLI/deep-links use
    srv = servers[branding.MCP_ID]
    assert srv["command"] == branding.PKG and srv["args"] == ["mcp"]  # `cold-frame mcp`
    # Claude Code doesn't reliably pass cwd to an MCP subprocess (#42687) → PROJECT_ROOT is required
    assert "PROJECT_ROOT" in srv.get("env", {})


def test_plugin_json_is_valid_and_its_references_exist() -> None:
    pj = _load(".claude-plugin/plugin.json")
    assert pj["name"]  # the `claude plugin install <name>` target
    assert (_PLUGIN / pj["hooks"]).is_file()  # hooks manifest reference resolves
    assert (_PLUGIN / pj["mcpServers"]).is_file()  # mcp manifest reference resolves
    # agent-push capture ships as a skill (no per-machine CLAUDE.md) — it must be present
    assert (_PLUGIN / "skills" / "remember-facts" / "SKILL.md").is_file()
