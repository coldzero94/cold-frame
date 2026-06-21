"""Local web UI (P3, ``[ui]`` extra) — read-mostly JSON over the same ``Memory``.

Served on ``branding.UI_PORT`` (127.0.0.1 only), with auto-fallback to the next free
port recorded in ``branding.UI_PORTFILE``. Heavy server deps stay behind the ``[ui]``
extra (I9); this package is an empty placeholder in the scaffold.
"""

from __future__ import annotations

__all__: list[str] = []
