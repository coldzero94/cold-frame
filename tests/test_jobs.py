"""Durable jobs queue tests (P4 unit 3, I12): lease / run_after / dedup / backoff / reclaim.

at-least-once + idempotent handlers = effectively-once; nothing is silently dropped
(exhausted jobs dead-letter). A settable clock drives backoff + stale-lease reclaim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cold_frame.constants import LEASE_TTL, MAX_ATTEMPTS
from cold_frame.forget.worker import Worker
from cold_frame.llm.base import HashEmbedder
from cold_frame.store.base import Job
from cold_frame.store.sqlite import SQLiteStore


class _Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


@pytest.fixture
def store_clock(db_path: str) -> tuple[SQLiteStore, _Clock]:
    clock = _Clock(datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC))
    store = SQLiteStore(db_path, embedder=HashEmbedder(), clock=clock)
    store.migrate()
    return store, clock


def test_enqueue_and_lease(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    jid = store.enqueue("reembed", {"batch": 1})
    job = store.lease_job(worker="w", now=clock.now())
    assert job is not None
    assert job.id == jid and job.kind == "reembed" and job.payload == {"batch": 1}
    assert job.status == "running" and job.attempts == 1
    assert store.lease_job(worker="w", now=clock.now()) is None  # nothing else runnable


def test_lease_respects_run_after(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    store.enqueue("consolidate", {}, run_after=clock.now() + timedelta(hours=1))
    assert store.lease_job(worker="w", now=clock.now()) is None  # scheduled in the future
    clock.t = clock.t + timedelta(hours=2)
    assert store.lease_job(worker="w", now=clock.now()) is not None


def test_dedup_debounces_pending(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    a = store.enqueue("consolidate", {}, dedup_key="consolidate:u")
    b = store.enqueue("consolidate", {}, dedup_key="consolidate:u")
    assert a == b  # debounced to the one pending job
    pending = store._conn.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0]
    assert pending == 1
    store.lease_job(worker="w", now=clock.now())
    store.finish_job(a, worker="w")
    c = store.enqueue("consolidate", {}, dedup_key="consolidate:u")  # key free again once done
    assert c != a


def test_finish_job_is_not_released(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    jid = store.enqueue("consolidate", {})
    store.lease_job(worker="w", now=clock.now())
    store.finish_job(jid, worker="w")
    assert store.lease_job(worker="w", now=clock.now()) is None


def test_finish_fail_fenced_on_locked_by(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    # a resurrected zombie worker must NOT finalize a job another worker now owns (I12)
    store, clock = store_clock
    jid = store.enqueue("consolidate", {})
    store.lease_job(worker="w1", now=clock.now())  # w1 owns it
    store.finish_job(jid, worker="w2")  # w2 is not the owner → no-op
    assert store._conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == (
        "running"
    )
    store.fail_job(jid, error="x", retry_after=None, worker="w2")  # also a no-op
    row = store._conn.execute("SELECT status, attempts FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"  # untouched by the foreign worker
    store.finish_job(jid, worker="w1")  # the real owner can finalize
    assert store._conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "done"


def test_fail_backs_off_then_dead_letters(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    jid = store.enqueue("consolidate", {})
    for _ in range(MAX_ATTEMPTS):
        assert store.lease_job(worker="w", now=clock.now()) is not None
        store.fail_job(jid, error="boom", retry_after=None, worker="w")
        clock.t = clock.t + timedelta(hours=1)  # past any backoff
    row = store._conn.execute("SELECT status, attempts FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "dead" and row["attempts"] == MAX_ATTEMPTS  # never silently dropped
    assert store.lease_job(worker="w", now=clock.now()) is None  # dead jobs are not leasable


def test_stale_lease_is_reclaimed(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    jid = store.enqueue("consolidate", {})
    assert store.lease_job(worker="w1", now=clock.now()) is not None  # running, locked now
    assert store.lease_job(worker="w2", now=clock.now()) is None  # not stale yet
    clock.t = clock.t + timedelta(seconds=LEASE_TTL + 1)  # crashed worker → lease expires
    reclaimed = store.lease_job(worker="w2", now=clock.now())
    assert reclaimed is not None and reclaimed.id == jid and reclaimed.attempts == 2


def test_worker_dispatches_and_finishes(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    seen: list[dict] = []  # type: ignore[type-arg]
    store.enqueue("consolidate", {"x": 1})
    worker = Worker(store, clock=clock, worker_id="w")
    assert worker.run_once({"consolidate": lambda job: seen.append(job.payload)}) is True
    assert seen == [{"x": 1}]
    assert store.lease_job(worker="w", now=clock.now()) is None  # finished, not re-leased
    assert worker.run_once({}) is False  # empty queue


def test_worker_failing_handler_reschedules(store_clock: tuple[SQLiteStore, _Clock]) -> None:
    store, clock = store_clock
    jid = store.enqueue("consolidate", {})
    worker = Worker(store, clock=clock, worker_id="w")

    def _boom(job: Job) -> None:
        raise RuntimeError("handler failed")

    worker.run_once({"consolidate": _boom})
    status = store._conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert status == "pending"  # rescheduled (backoff), never dropped
