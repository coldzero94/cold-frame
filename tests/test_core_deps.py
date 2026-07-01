"""I9 enforcement — the core engine imports pydantic + numpy ONLY.

CLAUDE.md I9 / §8: ``fastapi``/``psycopg`` (and the other extras: the mcp SDK, openai, sqlite-vec,
tiktoken, uvicorn, sentence-transformers, sqlcipher) live behind extras and are import-guarded, so
importing the core engine must NOT eagerly pull any of them in. This was a merge-gate invariant with
no enforcing test — a stray ``import fastapi`` in core would otherwise pass the whole suite.

Runs in a CLEAN subprocess so an extra already imported by a sibling test (e.g. the mcp SDK in
test_mcp.py) can't mask a real core leak.
"""

from __future__ import annotations

import subprocess
import sys

# The core engine surface (NOT cli/mcp/ui — those are allowed to import extras behind guards).
_CORE_MODULES = [
    "cold_frame",
    "cold_frame.api",
    "cold_frame.models",
    "cold_frame.constants",
    "cold_frame.exceptions",
    "cold_frame.observability",
    "cold_frame.write.core",
    "cold_frame.read.retrieve",
    "cold_frame.store.sqlite",
    "cold_frame.llm.base",
    "cold_frame.llm.tokens",
    "cold_frame.forget.consolidate",
    "cold_frame.procedural.optimize",
]
_FORBIDDEN = [
    "fastapi",
    "psycopg",
    "psycopg2",
    "openai",
    "uvicorn",
    "mcp",
    "tiktoken",
    "sentence_transformers",
    "sqlite_vec",
    "sqlcipher3",
]


def test_core_import_pulls_no_heavy_deps() -> None:
    code = (
        "import sys\n" + "".join(f"import {m}\n" for m in _CORE_MODULES) + f"bad = {_FORBIDDEN!r}\n"
        "hit = sorted(m for m in bad if m in sys.modules)\n"
        "assert not hit, 'core eagerly imported extras: ' + repr(hit)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
