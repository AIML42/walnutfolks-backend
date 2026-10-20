"""End-to-end tests covering the four success criteria from the brief."""

import asyncio

PAYLOAD = {
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": 1500,
    "currency": "INR",
}


async def test_health(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "HEALTHY"
    assert body["current_time"].endswith("Z")


async def test_webhook_accepts_quickly_and_returns_202(client):
    resp = await client.post("/v1/webhooks/transactions", json=PAYLOAD)
    assert resp.status_code == 202
    body = resp.json()
    assert body["transaction_id"] == PAYLOAD["transaction_id"]
    assert body["status"] == "ACCEPTED"


async def test_single_transaction_is_processed(client):
    txn = {**PAYLOAD, "transaction_id": "txn_single_001"}
    resp = await client.post("/v1/webhooks/transactions", json=txn)
    assert resp.status_code == 202

    # Immediately after accept it should be PROCESSING, not yet done.
    resp = await client.get(f"/v1/transactions/{txn['transaction_id']}")
    assert resp.status_code == 200
    record = resp.json()[0]
    assert record["status"] == "PROCESSING"
    assert record["processed_at"] is None

    # Wait out the (test-shortened) delay plus worker poll slack.
    await asyncio.sleep(2.0)

    resp = await client.get(f"/v1/transactions/{txn['transaction_id']}")
    record = resp.json()[0]
    assert record["status"] == "PROCESSED"
    assert record["processed_at"] is not None


async def test_duplicate_prevention(client):
    txn = {**PAYLOAD, "transaction_id": "txn_dup_002"}

    # Fire the same webhook five times, including concurrently.
    first = await client.post("/v1/webhooks/transactions", json=txn)
    assert first.json()["status"] == "ACCEPTED"

    results = await asyncio.gather(
        *[client.post("/v1/webhooks/transactions", json=txn) for _ in range(4)]
    )
    # Every call still returns 202 (handled gracefully)...
    assert all(r.status_code == 202 for r in results)
    # ...but all of them are recognised as duplicates.
    assert all(r.json()["status"] == "DUPLICATE" for r in results)

    await asyncio.sleep(2.0)

    # Exactly one processed transaction exists.
    resp = await client.get(f"/v1/transactions/{txn['transaction_id']}")
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "PROCESSED"


async def test_unknown_transaction_returns_404(client):
    resp = await client.get("/v1/transactions/does_not_exist")
    assert resp.status_code == 404


async def test_validation_rejects_bad_payload(client):
    bad = {**PAYLOAD, "amount": -5}
    resp = await client.post("/v1/webhooks/transactions", json=bad)
    assert resp.status_code == 422
