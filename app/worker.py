"""Background processor.

Design goals, in priority order:
  1. Never lose a transaction (reliability).
  2. Never process one twice (idempotency).
  3. Keep the webhook request path fast (performance).

How it works
------------
State lives in the database, not in memory. The webhook handler stores a row as
PROCESSING and returns immediately. This worker runs inside the app process as
an asyncio task and:

  * On startup, scans for every PROCESSING row and resumes it. That is the
    crash-recovery property: if the process died mid-flight, those rows are
    simply picked up again and finished.
  * Polls periodically for any PROCESSING rows it is not already handling.
  * For each, waits out the *remaining* portion of the 30-second delay (so a
    row created 10s before a restart only waits ~20 more seconds), then marks
    it PROCESSED.

Because completion goes through `mark_processed` (which only acts on rows still
PROCESSING), finishing the same transaction twice is a harmless no-op.
"""

import asyncio
import logging

from app.config import get_settings
from app.database import SessionLocal
from app.repository import list_pending, mark_processed, utcnow

logger = logging.getLogger("worker")


class TransactionWorker:
    def __init__(self) -> None:
        self._settings = get_settings()
        # Created in start() so it always binds to the loop the worker runs on
        # (lets the same worker be stopped and restarted on a fresh loop).
        self._stop: asyncio.Event | None = None
        self._loop_task: asyncio.Task | None = None
        # transaction_ids currently being processed, so a poll never spawns a
        # second coroutine for the same row.
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._loop_task = asyncio.create_task(self._run(), name="worker-loop")
        logger.info("worker started")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._loop_task:
            await self._loop_task
        # Cancel any in-flight processing tasks and await them so there are no
        # orphaned coroutines. Rows still PROCESSING are safe: the next startup
        # resumes them.
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._inflight.clear()
        logger.info("worker stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._dispatch_due_work()
            except Exception:  # pragma: no cover - defensive
                # A transient DB error must not kill the loop; log and retry.
                logger.exception("worker poll failed; will retry")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._settings.worker_poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass  # normal: the poll interval elapsed, loop again

    async def _dispatch_due_work(self) -> None:
        async with SessionLocal() as session:
            pending = await list_pending(session)

        for txn in pending:
            if txn.transaction_id in self._inflight:
                continue
            self._inflight.add(txn.transaction_id)
            task = asyncio.create_task(
                self._process(txn.transaction_id, txn.created_at)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _process(self, transaction_id: str, created_at) -> None:
        try:
            # Simulate the external payment call by waiting out whatever remains
            # of the configured delay, measured from when the webhook arrived.
            elapsed = (utcnow() - created_at).total_seconds()
            remaining = self._settings.processing_delay_seconds - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

            async with SessionLocal() as session:
                changed = await mark_processed(session, transaction_id)
            if changed:
                logger.info("processed %s", transaction_id)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            # Shutting down before completion is fine: the row is still
            # PROCESSING and will be resumed next time the worker starts.
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to process %s", transaction_id)
        finally:
            self._inflight.discard(transaction_id)
