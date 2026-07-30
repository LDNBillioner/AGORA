from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
import json
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
            if isinstance(raw, dict):
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
        db.close()


@tool("request_clarification", args_schema=RequestClarificationSchema)
def request_clarification(question: str) -> str:
    """
    Requests clarification from the user when important transaction parameters
    (like amount, item name, type, or category) are missing.
    Instead of hallucinating missing values, always use this tool to ask the user directly.
    """
    return f"CLARIFICATION_NEEDED: {question}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool Registry
# ─────────────────────────────────────────────────────────────────────────────
agent_tools = [record_transaction, request_clarification]
