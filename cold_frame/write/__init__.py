"""Write path (SPEC §4) — EXTRACT → WriteCore(ADMISSION → DEDUP → CONFLICT → PERSIST).

The ONE persist path (I15, D8): ``add()``, ``correct_memory()``, and every self-edit
tool converge on ``WriteCore.commit`` / ``WriteCore.commit_supersede``.
"""

from __future__ import annotations

from cold_frame.write.core import WriteCore

__all__ = ["WriteCore"]
