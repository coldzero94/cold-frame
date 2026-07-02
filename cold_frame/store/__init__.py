"""Store adapter seam (D10, sacred) — SQLiteStore is v1, PostgresStore later."""

from __future__ import annotations

from cold_frame.store.base import Event, Job, PurgeReport, Store

__all__ = ["Event", "Job", "PurgeReport", "Store"]
