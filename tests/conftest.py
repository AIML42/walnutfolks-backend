"""Test configuration.

These env vars are set *before* the app is imported so that the cached Settings
pick them up: an isolated temp SQLite database and a 1-second processing delay
so the full accept -> process -> PROCESSED cycle runs in a couple of seconds.
"""

import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["PROCESSING_DELAY_SECONDS"] = "1"
os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "0.2"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


@pytest_asyncio.fixture
async def client():
    # Imported here (not at module top) so the env vars above are already set.
    from app.database import init_db
    from app.main import app, worker

    await init_db()
    await worker.start()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await worker.stop()
