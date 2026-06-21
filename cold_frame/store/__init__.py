"""Store adapter seam (D10, 신성불가침) — SQLiteStore is v1, PostgresStore later."""

from __future__ import annotations

from cold_frame.store.base import Event, Job, PurgeReport, Store

__all__ = ["Event", "Job", "PurgeReport", "Store"]
