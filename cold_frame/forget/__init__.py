"""Forgetting / consolidation (SPEC §6) — decay, archive, episodic→semantic merge.

Non-destructive + convergent (re-run = no-op); pinned/high-importance never archived
(I13). Runs through the durable jobs queue (I12), never fire-and-forget.
"""

from __future__ import annotations

from cold_frame.forget.consolidate import Consolidator

__all__ = ["Consolidator"]
