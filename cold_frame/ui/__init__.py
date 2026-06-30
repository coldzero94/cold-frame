"""Local web UI (P3, ``[ui]`` extra) — read-mostly JSON over the same ``Memory``.

Served on ``branding.UI_PORT`` (127.0.0.1 only), with auto-fallback to the next free
port recorded in ``branding.UI_PORTFILE``. Built: ``server.py`` (stdlib HTTP + the JSON contract)
and ``contract.py`` (single-sourced wire types); the Vue SPA lives in ``frontend/`` and is served
from ``_dist``. Heavy server tooling stays a maintainer concern, not a runtime dep (I9).
"""

from __future__ import annotations

__all__: list[str] = []
