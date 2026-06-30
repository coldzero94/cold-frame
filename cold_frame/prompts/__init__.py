"""LLM prompt text + JSON-schema descriptions (build/prompts.md).

The canonical prompt for each task lives in its OWN submodule — ``extract``, ``conflict`` (dedup +
conflict), ``consolidate``, ``procedural`` (gradient), ``admission`` (I7 tiebreak), ``scope`` —
import from there. This package re-exports only ``EXTRACT_SYSTEM`` for ``write/extract``'s
convenience import. (It previously held empty-string placeholder constants that shadowed the real
submodule prompts; those were removed.)
"""

from __future__ import annotations

from cold_frame.prompts.extract import EXTRACT_SYSTEM

__all__ = ["EXTRACT_SYSTEM"]
