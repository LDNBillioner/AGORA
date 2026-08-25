"""Tests for the agent tool layer in tools.py (context, clarification, DB write)."""

import pytest

import tools
from tools import (
    set_tool_context,
    request_clarification,
    record_transaction,
    recap_transactions,
)


@pytest.fixture(autouse=True)
def _reset_context():
    tools._current_context.clear()
    yield
    tools._current_context.clear()


class TestToolContext:
    def test_set_tool_context(self):
        set_tool_context(tenant_id="tenant-x", user_id="628123")
        assert tools._current_context["tenant_id"] == "tenant-x"
        assert tools._current_context["user_id"] == "628123"


class TestRequestClarification:
    def test_returns_clarification_prefix(self):
        result = request_clarification.invoke(
            {"question": "Berapa nominalnya, Kak?"}
        )
        assert result.startswith("CLARIFICATION_NEEDED:")
        assert "Berapa nominalnya" in result


class TestRecordTransaction:
    def test_records_transaction_successfully(self):
        # Create schema on the test DATABASE_URL (file-based SQLite)
        from database import engine, Base
        import models  # noqa: F401

        Base.metadata.create_all(bind=engine)

        set_tool_context(tenant_id="tools-test-tenant", user_id="628999")
        result = record_transaction.invoke(
            {
                "items": [
                    {"item": "Kopi Susu", "quantity": 2, "price": 15000},
                ],
                "type": "income",
                "category": "Penjualan Produk",
            }
        )
        assert result.startswith("SUCCESS:"), result

        # Cleanup so other tests start clean
        from database import SessionLocal

        db = SessionLocal()
        try:
            db.query(models.Transaction).filter(
                models.Transaction.tenant_id == "tools-test-tenant"
            ).delete()
            db.query(models.User).filter(
                models.User.tenant_id == "tools-test-tenant"
            ).delete()
            db.query(models.Tenant).filter(
                models.Tenant.id == "tools-test-tenant"
            ).delete()
            db.commit()
        finally:
            db.close()

    def test_recap_transactions_runs(self):
        from database import engine, Base
        import models  # noqa: F401

        Base.metadata.create_all(bind=engine)

        set_tool_context(tenant_id="recap-test-tenant", user_id=None)
        result = recap_transactions.invoke({"timeframe": "all_time"})
        assert result.startswith("Rekap (all_time):")
        assert "Pemasukan" in result
