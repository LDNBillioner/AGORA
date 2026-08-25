from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
import json
import os
from datetime import datetime


# Schema Pydantic

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
    # Kolom Akuntansi
    document_type: Optional[str] = Field(None, description="Document type: FAKTUR_KREDIT, NOTA_KONTAN, STRUK, DELIVERY_ORDER, KUITANSI")
    invoice_number: Optional[str] = Field(None, description="Invoice/receipt number if available.")
    vendor_name: Optional[str] = Field(None, description="Vendor/supplier name.")
    tax_ppn: Optional[float] = Field(0, description="PPN tax amount.")
    discount_total: Optional[float] = Field(0, description="Total discount amount.")
    accounting_entries: Optional[List[dict]] = Field(None, description="Double-entry journal: [{account_code, account_name, debit, credit}]")
    is_math_verified: Optional[bool] = Field(True, description="Whether OCR math validation passed.")
    math_discrepancy: Optional[float] = Field(0, description="Math discrepancy amount if any.")


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


# Implementasi Alat (Tools)

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
    # Kolom akuntansi
    document_type: Optional[str] = None,
    invoice_number: Optional[str] = None,
    vendor_name: Optional[str] = None,
    tax_ppn: Optional[float] = 0,
    discount_total: Optional[float] = 0,
    accounting_entries: Optional[List[dict]] = None,
    is_math_verified: Optional[bool] = True,
    math_discrepancy: Optional[float] = 0,
) -> str:
    """
    Records a financial transaction with full accounting data into the PostgreSQL database.
    Includes document type classification, double-entry journal entries, and math verification.
    Call this tool ONLY when you have extracted sufficient information from the user's message.
    DO NOT hallucinate missing values — use request_clarification instead.
    """
    # Import di dalam fungsi untuk mencegah error circular import
    from database import SessionLocal
    import models

    try:
        db = SessionLocal()

        tenant_id = _current_context.get("tenant_id", "default-tenant")
        user_id = _current_context.get("user_id")

        # Pastikan tenant tersedia
        tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
        if not tenant:
            tenant = models.Tenant(id=tenant_id, name="Default Tenant")
            db.add(tenant)
            db.commit()

        # Normalisasi daftar item
        normalized_items = []
        for raw in items:
            if hasattr(raw, "model_dump"):
                normalized_items.append(raw.model_dump())
            elif hasattr(raw, "dict"):
                normalized_items.append(raw.dict())
            elif isinstance(raw, dict):
                normalized_items.append(raw)

        # Hitung total jika kosong
        if total_amount is None:
            total_amount = sum(
                float(i.get("price", 0)) * int(i.get("quantity", 1))
                for i in normalized_items
            )

        # Parsing tanggal transaksi
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
            merchant_name=merchant_name or vendor_name,
            transaction_date=txn_date,
            payment_method=payment_method,
            currency=currency or "IDR",
            # Kolom akuntansi
            document_type=document_type,
            invoice_number=invoice_number,
            vendor_name=vendor_name,
            tax_ppn=float(tax_ppn or 0),
            discount_total=float(discount_total or 0),
            accounting_entries=accounting_entries,
            is_math_verified=is_math_verified if is_math_verified is not None else True,
            math_discrepancy=float(math_discrepancy or 0),
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        # Simpan ke basis data vektor RAG
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
            # Jika RAG gagal, transaksi tetap dicatat
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
    
    # Coba ambil URL Cloudflare Tunnel
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
            
    # Gunakan localhost jika cloudflare mati
    if not public_url:
        public_url = os.getenv("AGORA_ENGINE_URL", "http://localhost:8000")
    
    # Buat tautan khusus pengguna
    if user_id:
        dashboard_url = f"{public_url}/dashboard/ui/{tenant_id}/{user_id}"
    else:
        dashboard_url = f"{public_url}/dashboard/ui/{tenant_id}"
    
    return f"DASHBOARD_URL: {dashboard_url}"


# Registrasi Alat (Tools)
agent_tools = [record_transaction, request_clarification, recap_transactions, get_dashboard_link]
