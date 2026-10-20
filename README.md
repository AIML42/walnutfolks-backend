# Transaction Webhook Service

A small, reliable service that receives transaction webhooks from a payment
processor (e.g. RazorPay), **acknowledges them instantly**, and **processes each
transaction exactly once in the background** with a simulated 30-second external
call — without ever losing a transaction, even across a crash.

Built with **FastAPI**, **SQLAlchemy (async)**, and a **database-backed
background worker**. Runs on SQLite with zero setup locally and on Postgres in
the cloud by changing one environment variable.

---

## Endpoints

| Method | Path                              | Purpose                                         |
|--------|-----------------------------------|-------------------------------------------------|
| `GET`  | `/`                               | Health check                                    |
| `POST` | `/v1/webhooks/transactions`       | Accept a webhook → returns **202** immediately  |
| `GET`  | `/v1/transactions/{transaction_id}` | Retrieve a transaction's status (for testing) |

Interactive API docs are available at `/docs` once the service is running.

### `POST /v1/webhooks/transactions`

Request:

```json
{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": 1500,
  "currency": "INR"
}
```

Response — **`202 Accepted`**:

```json
{ "transaction_id": "txn_abc123def456", "status": "ACCEPTED" }
```

A repeated webhook for the same `transaction_id` also returns `202`, but with
`"status": "DUPLICATE"` — and does **not** create a second transaction or
re-process it.

### `GET /v1/transactions/{transaction_id}`

```json
[{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": 1500.0,
  "currency": "INR",
  "status": "PROCESSED",
  "created_at": "2024-01-15T10:30:00Z",
  "processed_at": "2024-01-15T10:30:30Z"
}]
```

`status` is `PROCESSING` until the 30-second processing completes, at which point
it becomes `PROCESSED` and `processed_at` is set. Unknown ids return `404`.

---

## Run it locally

Requires Python 3.11+.

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the service (SQLite is the default — no database to set up)
uvicorn app.main:app --reload
```

The service is now at `http://127.0.0.1:8000`.

> **Tip:** the processing delay is 30 seconds by default (as the brief requires).
> To watch the full lifecycle quickly, lower it:
> `PROCESSING_DELAY_SECONDS=3 uvicorn app.main:app`

### Try it

```bash
# Health
curl http://127.0.0.1:8000/

# Send a webhook (responds in a few milliseconds with 202)
curl -X POST http://127.0.0.1:8000/v1/webhooks/transactions \
  -H 'Content-Type: application/json' \
  -d '{"transaction_id":"txn_abc123def456","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

# Check status immediately -> PROCESSING
curl http://127.0.0.1:8000/v1/transactions/txn_abc123def456

# Wait ~30s, check again -> PROCESSED, with processed_at set
curl http://127.0.0.1:8000/v1/transactions/txn_abc123def456
```

---

## Run the tests

The suite covers every success criterion (single transaction, duplicate
prevention, fast response, validation) and runs in a few seconds using a
shortened delay.

```bash
pip install -r requirements-dev.txt
pytest
```

---

## How it works

```
                 POST /v1/webhooks/transactions
                              │
                              ▼
          ┌──────────────────────────────────────┐
          │  1 atomic INSERT ... ON CONFLICT      │   ← idempotency + durability
          │  DO NOTHING  (status = PROCESSING)    │     in one indexed write
          └──────────────────────────────────────┘
                              │  (returns 202 in ~3ms)
                              ▼
                    [ persistent database ]
                              ▲
          ┌──────────────────────────────────────┐
          │  Background worker (async task)        │
          │   • on startup: resume any PROCESSING  │  ← crash recovery
          │   • wait out the 30s external call     │
          │   • UPDATE ... WHERE status=PROCESSING │  ← idempotent completion
          │       SET status=PROCESSED, processed_at│
          └──────────────────────────────────────┘
```

The webhook handler does the **minimum durable work** — a single indexed insert
— then returns `202`. All slow work happens in the background worker, off the
request path. Because every piece of state lives in the database (not in
memory), the service survives restarts and crashes.

### Why these choices

- **FastAPI + async** — natural fit for "accept fast, work later": the handler
  `await`s one tiny insert and returns. Measured webhook response time is ~3ms,
  far inside the 500ms budget, and it stays fast under processing load because
  the 30s work never touches the request path.

- **Idempotency at the database layer, not in application code.**
  `transaction_id` is the **primary key**, and inserts use
  `INSERT ... ON CONFLICT DO NOTHING`. This is a single atomic operation, so
  even identical webhooks arriving simultaneously can only ever create one row —
  there is no check-then-insert race to get wrong. The insert's return value
  tells us whether this was the first time we saw the id (`ACCEPTED`) or a
  duplicate (`DUPLICATE`).

- **A database-backed background worker instead of fire-and-forget.** The
  obvious shortcut — FastAPI `BackgroundTasks` — loses work if the process
  restarts mid-task. Here, a transaction is `PROCESSING` in the database the
  instant it is accepted, and the worker drives it to `PROCESSED`. **On startup
  the worker rescans for any `PROCESSING` rows and resumes them**, so a crash at
  any moment costs nothing — the transaction is simply finished on the next
  boot. (Verified by killing the server mid-processing and restarting: the
  transaction completed on its own, with the webhook never re-sent.)

- **Idempotent completion.** The worker marks a row done with
  `UPDATE ... WHERE status = 'PROCESSING'`. Running it twice is a harmless no-op,
  so overlapping work or a resumed task never double-processes or overwrites the
  original `processed_at`.

- **The 30-second delay is measured from receipt, not from pickup.** The worker
  waits out the *remaining* portion of the delay, so a transaction created 10s
  before a restart waits only ~20s more — predictable timing even across
  restarts.

- **SQLite locally, Postgres in the cloud, one URL apart.** SQLAlchemy's async
  layer means the code is identical for both; only `DATABASE_URL` changes.
  Provider URLs like `postgres://...` are automatically rewritten to the async
  `postgresql+asyncpg://...` driver, so you can paste them verbatim.

### Honest trade-offs

- The worker runs **in the same process** as the API. This keeps deployment to a
  single service — ideal for this scope — and is fully crash-safe thanks to
  startup recovery. If throughput ever demanded it, the same code could run as a
  **separate worker process** (the DB-backed design needs no change): add
  `SELECT ... FOR UPDATE SKIP LOCKED` to the pending query so multiple workers
  claim distinct rows, and run the worker loop in its own container.
- Scheduling uses lightweight DB polling (every couple of seconds). At this scale
  that is simpler and more robust than adding a broker like Redis/Celery; the
  README notes above show the path to scale out if needed.

---

## Deploy to the cloud

The repo includes a `Dockerfile`, a `render.yaml` blueprint, and a `Procfile`,
so it runs on any container host. The quickest path (Render, free tier):

1. Push this repo to GitHub.
2. On [Render](https://render.com): **New → Blueprint**, pick the repo.
   `render.yaml` provisions a free Postgres database and wires its connection
   string into the service as `DATABASE_URL` automatically.
3. Deploy. The public URL's `/` returns `{"status":"HEALTHY", ...}`.

Any other host works the same way — build the Docker image (or use the
`Procfile`) and set `DATABASE_URL` to your Postgres instance. The platform's
injected `$PORT` is honoured automatically.

---

## Configuration

| Variable                         | Default                               | Description                                  |
|----------------------------------|---------------------------------------|----------------------------------------------|
| `DATABASE_URL`                   | `sqlite+aiosqlite:///./transactions.db` | Async SQLAlchemy URL (SQLite or Postgres)  |
| `PROCESSING_DELAY_SECONDS`       | `30`                                  | Simulated external-call delay                |
| `WORKER_POLL_INTERVAL_SECONDS`   | `2`                                   | How often the worker scans for due work      |

---

## Project layout

```
app/
  config.py      # env-driven settings (+ async-driver URL normalisation)
  database.py    # async engine / session
  models.py      # Transaction table (transaction_id is the primary key)
  schemas.py     # request/response validation
  repository.py  # idempotent insert + idempotent completion
  worker.py      # background processor with crash recovery
  main.py        # FastAPI app and the three endpoints
tests/
  test_api.py    # covers all four success criteria
Dockerfile, render.yaml, Procfile   # deployment
```
