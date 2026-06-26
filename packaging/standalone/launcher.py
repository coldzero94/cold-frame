"""PyInstaller entry shim — freezes the cold-frame CLI into a self-contained binary.

A standalone binary lets users run cold-frame with NO Python installed: download one file, put it
on PATH, done (GitHub Releases / `curl | sh`). It is the same CLI as `pip install`, just frozen.
See build.sh + README.md in this directory.
"""

from __future__ import annotations

import sys

from cold_frame.cli import main

if __name__ == "__main__":
    sys.exit(main())
