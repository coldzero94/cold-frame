"""In-process durable worker (I12): lease → handle → finish/fail, no fire-and-forget.

A failing handler does not lose the job — it is rescheduled with backoff (or dead-lettered
after max attempts) by Store.fail_job; if fail_job ITSELF fails, the job is left ``running``
and recovered by the stale-lease reclaim (lease past LEASE_TTL). Handlers must be idempotent
(at-least-once + idempotent = effectively-once). One worker per process polls; multiple are
safe (DB row lease serializes claims).
"""

from __future__ import annotations

from collections.abc import Callable

from cold_frame.exceptions import StoreError
from cold_frame.llm.base import Clock
from cold_frame.observability import get_logger
from cold_frame.store.base import Job, Store

Handler = Callable[[Job], None]

_log = get_logger(__name__)


class Worker:
    """Leases and runs one job per ``run_once``; the caller loops/polls."""

    def __init__(self, store: Store, *, clock: Clock, worker_id: str = "worker") -> None:
        self._store = store
        self._clock = clock
        self._id = worker_id

    def run_once(self, handlers: dict[str, Handler]) -> bool:
        """Lease + run one runnable job. Returns False if the queue had nothing runnable."""
        job = self._store.lease_job(worker=self._id, now=self._clock.now())
        if job is None:
            return False
        try:
            handler = handlers.get(job.kind)
            if handler is None:
                raise KeyError(f"no handler for job kind {job.kind!r}")
            handler(job)
            self._store.finish_job(job.id)
        except Exception as exc:  # handler/config failure → reschedule or dead-letter
            try:
                self._store.fail_job(job.id, error=f"{type(exc).__name__}: {exc}", retry_after=None)
            except StoreError:  # even fail_job failed → leave running; stale-reclaim recovers it
                _log.error("fail_job_failed", extra={"job_id": job.id, "kind": job.kind})
        return True
