"""CI guard: assert the SPA bundle is really built before packaging the wheel.

Run AFTER ``pnpm -C frontend build`` and BEFORE ``uv build``. The _dist assets are git-ignored
(built in CI, force-shipped via [tool.hatch ... artifacts]); this fails LOUDLY if _dist still
holds only the .gitkeep placeholder, so a release never ships a blank UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_INDEX = Path(__file__).resolve().parents[1] / "cold_frame" / "ui" / "_dist" / "index.html"

if not _INDEX.is_file() or "assets/" not in _INDEX.read_text(encoding="utf-8"):
    sys.exit("UI bundle missing — run `pnpm -C frontend build` before building the wheel")
print(f"UI bundle present: {_INDEX}")
