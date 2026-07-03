"""cold-frame — a local-first, ownable memory layer for LLM agents.

One SQLite file holds facts + BM25 + vectors + edges + versions + provenance.
Works offline, no key, no server.
"""

from __future__ import annotations

from cold_frame.api import Memory
from cold_frame.models import Note, Scope, SearchResult, Source

__version__ = "0.1.1"

__all__ = [
    "Memory",
    "Note",
    "Scope",
    "SearchResult",
    "Source",
    "__version__",
]
