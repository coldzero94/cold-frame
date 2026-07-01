"""Smoke test for the shipped examples — so they can't rot as the API evolves."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_QUICKSTART = Path(__file__).resolve().parent.parent / "examples" / "quickstart.py"


def test_quickstart_example_runs() -> None:
    proc = subprocess.run(
        [sys.executable, str(_QUICKSTART)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "corrected" in proc.stdout and "archived" in proc.stdout  # the supersede loop ran
