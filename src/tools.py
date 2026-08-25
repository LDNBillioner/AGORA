from langchain_core.tools import tool
# NOTE: langchain-core's @tool expects *pydantic v1* args_schema models,
# so we must use the pydantic.v1 compatibility namespace here.
from pydantic.v1 import BaseModel, Field
from typing import Optional, List
import uuid
import json
import os
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class TransactionItemSchema(BaseModel):
    item: str = Field(..., description="Name of the item or service.")
    quantity: int = Field(..., description="Quantity of the item.", ge=1)
    price: float = Field(..., description="Price per unit.", ge=0)


class RecordTransactionSchema(BaseModel):
    items: List[TransactionItemSchema] = Field(
        ..., description="List of items in the transaction."
    )
    type: str = Field(
        ...,
        description="Transaction type: 'income' (pemasukan) or 'expense' (pengeluaran).",
    )
    category: str = Field(
        ...,
        description=(
            "Category of the transaction, e.g., 'Gaji', 'Belanja Bahan Baku', "
            "'Penjualan', 'Listrik', 'Transportasi', etc."
        ),
    )
    total_amount: Optional[float] = Field(
        None, description="Total amount. If None, will be calculated from items."
    )
    merchant_name: Optional[str] = Field(None, description="Name of the merchant or store.")
    transaction_date: Optional[str] = Field(
        None, description="Date of the transaction in ISO format (YYYY-MM-DD)."
    )
    payment_method: Optional[str] = Field(
        None, description="Payment method: Cash, QRIS, Transfer, Kartu Debit, etc."
    )
    notes: Optional[str] = Field(None, description="Any additional notes.")
    currency: Optional[str] = Field("IDR", description="Currency code, default IDR.")


class RequestClarificationSchema(BaseModel):
    question: str = Field(
        ...,
        description=(
            "The clarification question to ask the user in Bahasa Indonesia, "
            "e.g., 'Berapa nominal gajinya, Kak?'."
        ),
    )


class RecapTransactionsSchema(BaseModel):
    timeframe: str = Field(
        ...,
        description="Timeframe for recap. e.g., 'today', 'this_week', 'this_month', 'all_time'."
    )


class GetDashboardLinkSchema(BaseModel):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Tool Implementations
# ─────────────────────────────────────────────────────────────────────────────

# NOTE: tenant_id and user_id are injected from the agent state via a
#       module-level context variable before the agent graph is invoked.
# This avoids threading complexity with LangChain's tool interface.
_current_context: dict = {}


def set_tool_context(tenant_id: str, user_id: Optional[str] = None):
    """Call this before invoking the agent graph to set the DB context."""
    _current_context["tenant_id"] = tenant_id
    _current_context["user_id"] = user_id


@tool("record_transaction", args_schema=RecordTransactionSchema)
def record_transaction(
    items: List[dict],
    type: str,
    category: str,
    total_amount: Optional[float] = None,
    merchant_name: Optional[str] = None,
    transaction_date: Optional[str] = None,
    payment_method: Optional[str] = None,
    notes: Optional[str] = None,
    currency: Optional[str] = "IDR",
) -> str:
    """
    Records a financial transaction into the PostgreSQL database.
    Call this tool ONLY when you have extracted sufficient information
    (items with their prices, transaction type, and category) from the user's message.
    DO NOT hallucinate missing values — use request_clarification instead.
    """
    # Import here to avoid circular imports at module load
    from database import SessionLocal
    import models

    db = None
    try:
        db = SessionLocal()

        tenant_id = _current_context.get("tenant_id", "default-tenant")
        user_id = _current_context.get("user_id")

        # Ensure tenant exists
        tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
        if not tenant:
            tenant = models.Tenant(id=tenant_id, name="Default Tenant")
            db.add(tenant)
            db.commit()

        # Normalize items
        normalized_items = []
        for raw in items:
            if hasattr(raw, "model_dump"):
                normalized_items.append(raw.model_dump())
            elif hasattr(raw, "dict"):
                normalized_items.append(raw.dict())
            elif isinstance(raw, dict):
                normalized_items.append(raw)

        # Calculate total if not provided
        if total_amount is None:
            total_amount = sum(
                float(i.get("price", 0)) * int(i.get("quantity", 1))
                for i in normalized_items
            )

        # Parse transaction date
        txn_date = None
        if transaction_date:
            try:
                txn_date = datetime.fromisoformat(transaction_date.replace("Z", "+00:00"))
            except ValueError:
                pass

        transaction_id = str(uuid.uuid4())

        db_transaction = models.Transaction(
            id=transaction_id,
            tenant_id=tenant_id,
            user_id=user_id,
            source="whatsapp",
            items=normalized_items,
            total_amount=float(total_amount),
            type=type,
            category=category,
            notes=notes,
            merchant_name=merchant_name,
            transaction_date=txn_date,
            payment_method=payment_method,
            currency=currency or "IDR",
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        # ── Add to RAG vector store for future context ──────────────────────
        try:
            from rag import add_transaction_to_rag
            rag_text = (
                f"[{type.upper()}] {category} — "
                + ", ".join(
                    f"{i.get('item')} x{i.get('quantity')} @{i.get('price')}"
                    for i in normalized_items
                )
                + f" | Total: {total_amount} {currency}"
            )
            add_transaction_to_rag(
                tenant_id=tenant_id,
                text=rag_text,
                metadata={
                    "transaction_id": transaction_id,
                    "type": type,
                    "category": category,
                    "total_amount": total_amount,
                },
            )
        except Exception as rag_err:
            # RAG indexing failure must not block transaction recording
            print(f"[RAG] Failed to index transaction: {rag_err}")

        return (
            f"SUCCESS: Transaksi berhasil dicatat! "
            f"ID: {transaction_id} | Tipe: {type} | Kategori: {category} | "
            f"Total: Rp {total_amount:,.0f}"
        )

    except Exception as exc:
        return f"ERROR: Gagal menyimpan transaksi — {str(exc)}"
    finally:
        if db is not None:
            db.close()


@tool("request_clarification", args_schema=RequestClarificationSchema)
def request_clarification(question: str) -> str:
    """
    Requests clarification from the user when important transaction parameters
    (like amount, item name, type, or category) are missing.
    Instead of hallucinating missing values, always use this tool to ask the user directly.
    """
    return f"CLARIFICATION_NEEDED: {question}"


@tool("recap_transactions", args_schema=RecapTransactionsSchema)
def recap_transactions(timeframe: str) -> str:
    """
    Returns a financial recap (total income, expense, and net profit) for the specified timeframe.
    Timeframe options: 'today', 'this_week', 'this_month', 'all_time'.
    """
    from database import SessionLocal
    import models
    from datetime import datetime, timedelta
    from sqlalchemy import func
    
    tenant_id = _current_context.get("tenant_id", "default-tenant")
    user_id = _current_context.get("user_id", "")
    
    db = SessionLocal()
    try:
        query = db.query(models.Transaction).filter(models.Transaction.tenant_id == tenant_id)
        if user_id:
            query = query.filter(models.Transaction.user_id == user_id)
        
        now = datetime.now()
        
        if timeframe == 'today':
            query = query.filter(func.date(models.Transaction.created_at) == now.date())
        elif timeframe == 'this_week':
            start_of_week = now - timedelta(days=now.weekday())
            query = query.filter(models.Transaction.created_at >= start_of_week.date())
        elif timeframe == 'this_month':
            query = query.filter(
                func.extract('month', models.Transaction.created_at) == now.month,
                func.extract('year', models.Transaction.created_at) == now.year
            )
        
        transactions = query.all()
        
        total_income = sum(t.total_amount for t in transactions if t.type == "income")
        total_expense = sum(t.total_amount for t in transactions if t.type == "expense")
        net_profit = total_income - total_expense
        count = len(transactions)
        
        return (
            f"Rekap ({timeframe}):\n"
            f"- Pemasukan: Rp {total_income:,.0f}\n"
            f"- Pengeluaran: Rp {total_expense:,.0f}\n"
            f"- Profit Bersih: Rp {net_profit:,.0f}\n"
            f"- Jumlah Transaksi: {count}"
        )
    finally:
        db.close()


@tool("get_dashboard_link", args_schema=GetDashboardLinkSchema)
def get_dashboard_link() -> str:
    """
    Returns the visual web dashboard URL for the current user's personal dashboard.
    Use this tool when the user asks to view charts, statistics, or the visual dashboard link.
    """
    tenant_id = _current_context.get("tenant_id", "default-tenant")
    user_id = _current_context.get("user_id", "")
    
    # Try to dynamically get the Cloudflare Tunnel URL
    public_url = os.getenv("PUBLIC_URL", "")
    if not public_url:
        import urllib.request
        import re
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:20241/metrics", timeout=2)
            metrics = resp.read().decode("utf-8")
            match = re.search(r"https://[a-z-]*\.trycloudflare\.com", metrics)
            if match:
                public_url = match.group(0)
        except Exception:
            pass
            
    # Fallback to localhost if cloudflare is not running
    if not public_url:
        public_url = os.getenv("AGORA_ENGINE_URL", "http://localhost:8000")
    
    # Generate per-user link
    if user_id:
        dashboard_url = f"{public_url}/dashboard/ui/{tenant_id}/{user_id}"
    else:
        dashboard_url = f"{public_url}/dashboard/ui/{tenant_id}"
    
    return f"DASHBOARD_URL: {dashboard_url}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool Registry
# ─────────────────────────────────────────────────────────────────────────────
agent_tools = [record_transaction, request_clarification, recap_transactions, get_dashboard_link]
