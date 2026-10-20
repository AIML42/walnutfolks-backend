"""Database operations, kept separate from the HTTP and worker layers.

The two functions that matter for correctness are `insert_if_absent` (idempotent
accept) and `mark_processed` (idempotent completion). Both are written so that
running them twice is harmless.
"""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction, TransactionStatus
from app.schemas import WebhookIn


def utcnow() -> datetime:
    """Naive UTC timestamp (consistent with how the columns are stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _insert_stmt(dialect_name: str):
    """ON CONFLICT DO NOTHING is spelled differently per dialect, but the intent
    is identical: insert the row, and if the primary key already exists, do
    nothing instead of raising. This is the atomic heart of idempotency."""
    if dialect_name == "postgresql":
        return pg_insert(Transaction)
    # sqlite insert supports on_conflict_do_nothing; good enough for local/dev.
    return sqlite_insert(Transaction)


async def insert_if_absent(session: AsyncSession, payload: WebhookIn) -> bool:
    """Insert a new PROCESSING transaction. Returns True if a new row was
    created, False if this transaction_id was already present (a duplicate).

    The uniqueness check and the insert happen in a single atomic statement, so
    even two identical webhooks arriving at the same instant cannot both create
    a row — exactly one wins, the other is a no-op.
    """
    dialect = session.bind.dialect.name
    stmt = (
        _insert_stmt(dialect)
        .values(
            transaction_id=payload.transaction_id,
            source_account=payload.source_account,
            destination_account=payload.destination_account,
            amount=payload.amount,
            currency=payload.currency,
            status=TransactionStatus.PROCESSING,
            created_at=utcnow(),
            processed_at=None,
        )
        .on_conflict_do_nothing(index_elements=["transaction_id"])
    )
    result = await session.execute(stmt)
    await session.commit()
    # rowcount is 1 when a row was inserted, 0 when the conflict skipped it.
    return result.rowcount == 1


async def get_transaction(
    session: AsyncSession, transaction_id: str
) -> Transaction | None:
    return await session.get(Transaction, transaction_id)


async def list_pending(session: AsyncSession) -> list[Transaction]:
    """All transactions still awaiting processing. Used by the worker to find
    work on startup (crash recovery) and on each poll."""
    result = await session.execute(
        select(Transaction).where(
            Transaction.status == TransactionStatus.PROCESSING
        )
    )
    return list(result.scalars().all())


async def mark_processed(session: AsyncSession, transaction_id: str) -> bool:
    """Flip a transaction to PROCESSED and stamp processed_at — but only if it
    is still PROCESSING. The WHERE clause makes this a no-op the second time,
    so duplicate completions never overwrite the original timestamp."""
    result = await session.execute(
        update(Transaction)
        .where(
            Transaction.transaction_id == transaction_id,
            Transaction.status == TransactionStatus.PROCESSING,
        )
        .values(status=TransactionStatus.PROCESSED, processed_at=utcnow())
    )
    await session.commit()
    return result.rowcount == 1
