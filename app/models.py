"""Persistent storage model for transactions."""

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransactionStatus:
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"


class Transaction(Base):
    __tablename__ = "transactions"

    # transaction_id is the PRIMARY KEY. This single fact is what makes the
    # service idempotent: the database physically cannot hold two rows with the
    # same id, so duplicate webhooks collapse into one row regardless of timing.
    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)

    source_account: Mapped[str] = mapped_column(String, nullable=False)
    destination_account: Mapped[str] = mapped_column(String, nullable=False)

    # Numeric keeps money exact (no float rounding). Handles 1500 and 150.50.
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # PROCESSING right after the webhook is accepted; PROCESSED once the
    # (simulated) 30-second external call completes.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=TransactionStatus.PROCESSING, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "source_account": self.source_account,
            "destination_account": self.destination_account,
            # Numeric comes back as Decimal; float() gives clean JSON numbers.
            "amount": float(self.amount),
            "currency": self.currency,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "processed_at": _iso(self.processed_at),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # Stored as naive UTC; present it with a trailing Z so clients read it as UTC.
    return value.replace(microsecond=0).isoformat() + "Z"
