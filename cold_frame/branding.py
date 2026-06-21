"""Branding indirection — the ONE place literal name/path/port strings live.

A rename (PyPI/trademark resolution of D19 vs D-P2) is a single-file edit, not a
sweep. Forbid literal ``cold-frame`` / ports / scheme strings elsewhere (grep check).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# --- identity ---
PKG: Final[str] = "cold-frame"  # distribution / PyPI name, MCP server id, URL scheme stem
IMPORT: Final[str] = "cold_frame"  # importable Python package name
MCP_ID: Final[str] = "cold-frame"  # `claude mcp add cold-frame -- cold-frame mcp`
URL_SCHEME: Final[str] = "cold-frame"  # cold-frame://fact/{id} resources

# --- on-disk locations ---
DB_DIR: Final[Path] = Path.home() / ".cold-frame"
DB_PATH: Final[Path] = DB_DIR / "memory.db"

# --- local UI server (the only thing that has a port; DB is a file, no port) ---
UI_HOST: Final[str] = "127.0.0.1"  # localhost-only bind (CSRF / DNS-rebind safety)
UI_PORT: Final[int] = 27182  # deliberately uncommon; auto-fallback to next free port if taken
UI_PORTFILE: Final[Path] = DB_DIR / "ui.port"  # resolved port recorded here for deep-links


def ui_base_url(port: int = UI_PORT) -> str:
    """Base URL of the local web UI for a resolved port."""
    return f"http://localhost:{port}"


def fact_deeplink(note_id: str, *, port: int = UI_PORT) -> str:
    """Deep-link included in every MCP tool result (SPEC §8)."""
    return f"{ui_base_url(port)}/fact/{note_id}"


def resource_uri(note_id: str) -> str:
    """MCP resource URI ``cold-frame://fact/{id}`` (SPEC §8)."""
    return f"{URL_SCHEME}://fact/{note_id}"
