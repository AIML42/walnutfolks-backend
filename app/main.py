"""FastAPI application: webhook intake, health check, and transaction query.

The webhook handler does the minimum amount of work needed to *durably accept*
the transaction (one indexed insert) and returns 202. All slow work — the
30-second processing — happens in the background worker, off the request path.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.database import get_session, init_db
from app.repository import utcnow
from app.schemas import HealthResponse, WebhookAccepted, WebhookIn
from app.worker import TransactionWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

settings = get_settings()
worker = TransactionWorker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the table exists, then start the background worker. The worker's
    # first action is to resume any transactions left PROCESSING by a previous
    # run — crash recovery happens automatically here.
    await init_db()
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(
    title="Transaction Webhook Service",
    version="1.0.0",
    description=(
        "Accepts payment webhooks, acknowledges within milliseconds, and "
        "processes each transaction exactly once in the background."
    ),
    lifespan=lifespan,
)


@app.get("/", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Liveness/health check."""
    return HealthResponse(
        status="HEALTHY",
        current_time=utcnow().replace(microsecond=0).isoformat() + "Z",
    )


@app.post(
    "/v1/webhooks/transactions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAccepted,
    tags=["webhooks"],
)
async def receive_webhook(
    payload: WebhookIn,
    session: AsyncSession = Depends(get_session),
) -> WebhookAccepted:
    """Accept a transaction webhook.

    Returns 202 immediately. A single atomic insert both persists the
    transaction and enforces idempotency: a duplicate transaction_id is
    recognised and acknowledged without creating a second row or re-processing.
    """
    created = await repository.insert_if_absent(session, payload)
    return WebhookAccepted(
        transaction_id=payload.transaction_id,
        status="ACCEPTED" if created else "DUPLICATE",
    )


@app.get("/v1/transactions/{transaction_id}", tags=["transactions"])
async def get_transaction(
    transaction_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Fetch a transaction's current status and timing (for testing).

    Returns a single-element JSON array to match the documented response shape,
    or 404 if the transaction_id is unknown.
    """
    txn = await repository.get_transaction(session, transaction_id)
    if txn is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "transaction not found"},
        )
    return JSONResponse(content=[txn.to_dict()])
