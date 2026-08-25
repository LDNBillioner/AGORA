"""Tests for the AGORA AI Engine HTTP API."""

import uuid


class TestHealth:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "running" in body["message"].lower()
        assert body["version"] == "1.0.0"


class TestWebhookVerification:
    def test_success_returns_challenge(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "agora_verify_token",
                "hub.challenge": 12345,
            },
        )
        assert resp.status_code == 200
        assert resp.text == "12345"

    def test_failure_on_wrong_token(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": 12345,
            },
        )
        assert resp.status_code == 403

    def test_post_webhook_always_ok(self, client):
        # Meta requires a 200 even for payloads we can't parse.
        resp = client.post("/webhook", json={})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestTransactions:
    def _payload(self, **overrides):
        payload = {
            "items": [
                {"item": "Kopi Susu", "quantity": 2, "price": 15000},
            ],
            "type": "income",
            "category": "Penjualan Produk",
        }
        payload.update(overrides)
        return payload

    def test_save_and_list(self, client):
        resp = client.post("/transactions", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        transaction_id = body["transaction_id"]

        listing = client.get("/transactions").json()
        ids = [t["id"] for t in listing["transactions"]]
        assert transaction_id in ids

    def test_total_computed_from_items(self, client):
        resp = client.post("/transactions", json=self._payload())
        tid = resp.json()["transaction_id"]
        listing = client.get("/transactions").json()
        txn = next(t for t in listing["transactions"] if t["id"] == tid)
        assert txn["total_amount"] == 30000

    def test_rejects_missing_items(self, client):
        resp = client.post("/transactions", json={"notes": "no items"})
        assert resp.status_code == 400

    def test_rejects_invalid_quantity(self, client):
        resp = client.post(
            "/transactions",
            json=self._payload(items=[{"item": "X", "quantity": 0, "price": 5}]),
        )
        assert resp.status_code == 422


class TestDashboardSummary:
    def test_summary_totals(self, client):
        tenant = f"tenant-{uuid.uuid4().hex[:8]}"
        client.post(
            "/transactions",
            json={
                "tenant_id": tenant,
                "items": [{"item": "Sale", "quantity": 1, "price": 100000}],
                "type": "income",
                "category": "Penjualan",
            },
        )
        client.post(
            "/transactions",
            json={
                "tenant_id": tenant,
                "items": [{"item": "Bahan", "quantity": 3, "price": 10000}],
                "type": "expense",
                "category": "Bahan Baku",
            },
        )

        summary = client.get(
            "/dashboard/summary", params={"tenant_id": tenant}
        ).json()["summary"]
        assert summary["total_income"] == 100000
        assert summary["total_expense"] == 30000
        assert summary["net_profit"] == 70000
        assert summary["transaction_count"] == 2


class TestUserManagement:
    def test_register_user_requires_existing_tenant(self, client):
        resp = client.post(
            "/users",
            json={
                "phone_number": "628111111111",
                "tenant_id": "no-such-tenant",
            },
        )
        assert resp.status_code == 404

    def test_register_and_duplicate_user(self, client):
        tenant = f"tenant-{uuid.uuid4().hex[:8]}"
        # Creating a transaction auto-creates the tenant
        client.post(
            "/transactions",
            json={
                "tenant_id": tenant,
                "items": [{"item": "A", "quantity": 1, "price": 1000}],
            },
        )
        resp = client.post(
            "/users",
            json={
                "phone_number": "628222222222",
                "name": "Kasir A",
                "role": "employee",
                "tenant_id": tenant,
            },
        )
        assert resp.status_code == 200

        duplicate = client.post(
            "/users",
            json={
                "phone_number": "628222222222",
                "tenant_id": tenant,
            },
        )
        assert duplicate.status_code == 409

        users = client.get(f"/tenants/{tenant}/users").json()["users"]
        assert any(u["phone_number"] == "628222222222" for u in users)
