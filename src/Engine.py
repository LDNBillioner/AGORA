"""
Engine.py — AGORA AI Engine (FastAPI)

Endpoints:
  GET  /                    — Health check
  GET  /webhook             — Meta webhook verification handshake
  POST /webhook             — WhatsApp Cloud API message receiver (async background)
  POST /extract-receipt     — NVIDIA Nemotron-OCR receipt extraction
  POST /transactions        — Manually save a transaction
  GET  /transactions        — List all transactions (raw)
  GET  /dashboard/summary   — Financial summary per tenant (pemasukan/pengeluaran/net)
  GET  /dashboard/transactions — Filtered & paginated transaction list for dashboard
  POST /users               — Register a new user/employee
  GET  /tenants/{tenant_id}/users — List all users in a tenant
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Query, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import SessionLocal
import models
import os
import base64
import httpx
import json
import uuid
import re
import asyncio
from datetime import datetime, timezone, date
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="AGORA AI Engine",
    description="WhatsApp AI Accountant Backend — B2B Micro-SaaS for UMKM Indonesia",
    version="1.0.0",
)

# Allow dashboard frontend to call these endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "agora_verify_token")


# ─────────────────────────────────────────────────────────────────────────────
# Database Dependency
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Numeric Normalizers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_numeric_token(token: str) -> float | None:
    if token is None:
        return None
    normalized = str(token).strip()
    if not normalized:
        return None
    normalized = re.sub(r"[^0-9,\.\-]", "", normalized)
    if not re.search(r"\d", normalized):
        return None
    normalized = normalized.replace(" ", "")
    comma_count = normalized.count(",")
    dot_count = normalized.count(".")
    if comma_count > 0 and dot_count > 0:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "")
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif comma_count > 0:
        parts = normalized.split(",")
        if comma_count > 1 or len(parts[-1]) == 3:
            normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(",", ".")
    elif dot_count > 1:
        normalized = normalized.replace(".", "")
    elif dot_count == 1:
        parts = normalized.split(".")
        if len(parts[-1]) == 3:
            normalized = normalized.replace(".", "")
    try:
        return float(normalized)
    except ValueError:
        return None


def normalize_integer_token(token) -> int | None:
    value = normalize_numeric_token(str(token))
    if value is None:
        return None
    if not float(value).is_integer():
        return None
    return int(value)


# ─────────────────────────────────────────────────────────────────────────────
# OCR Helpers (unchanged from original, kept for /extract-receipt)
# ─────────────────────────────────────────────────────────────────────────────

def normalize_ocr_payload(extracted_text: str) -> dict:
    """Parse OCR output into structured dict. Handles both new accounting schema and legacy format."""
    cleaned_text = re.sub(r'```(?:json)?\n?|```', '', extracted_text).strip()
    try:
        parsed = json.loads(cleaned_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"raw_text": extracted_text, "line_items": [], "items": [], "financial_summary": {"grand_total": 0}, "currency": "IDR"}


def _convert_new_schema_to_legacy(payload: dict) -> dict:
    """
    Convert the new accounting schema to a backward-compatible format
    that the rest of the pipeline (agent, tools) can consume.
    Preserves all accounting data in extra fields.
    """
    doc_meta = payload.get("document_metadata", {})
    line_items = payload.get("line_items", [])
    fin_summary = payload.get("financial_summary", {})
    acct_entries = payload.get("accounting_entries", [])

    # Convert line_items to legacy items format
    legacy_items = []
    for li in line_items:
        legacy_items.append({
            "item": li.get("description", ""),
            "quantity": li.get("quantity", 1),
            "price": li.get("line_total_net", 0) or li.get("unit_price_effective", 0),
            # Preserve extra accounting fields
            "item_code": li.get("item_code"),
            "unit": li.get("unit", "pcs"),
            "unit_price_effective": li.get("unit_price_effective", 0),
            "discount_amount": li.get("discount_amount", 0),
            "line_total_net": li.get("line_total_net", 0),
            "account_mapping": li.get("account_mapping", ""),
        })

    vendor = doc_meta.get("vendor", {})

    return {
        "merchant_name": vendor.get("name", "") if isinstance(vendor, dict) else str(vendor),
        "transaction_date": doc_meta.get("transaction_date"),
        "payment_method": None,
        "items": legacy_items,
        "total_amount": fin_summary.get("grand_total", 0),
        "currency": "IDR",
        # Accounting-specific fields
        "document_type": doc_meta.get("document_type", "STRUK"),
        "invoice_number": doc_meta.get("invoice_number"),
        "vendor_name": vendor.get("name", "") if isinstance(vendor, dict) else str(vendor),
        "due_date": doc_meta.get("due_date"),
        "customer": doc_meta.get("customer", {}),
        "tax_ppn": fin_summary.get("tax_ppn", 0),
        "discount_total": fin_summary.get("total_discount", 0),
        "subtotal": fin_summary.get("subtotal", 0),
        "is_math_verified": fin_summary.get("is_math_verified", True),
        "math_discrepancy": fin_summary.get("math_discrepancy_amount", 0),
        "accounting_entries": acct_entries,
    }


def fallback_parse_items(payload: dict) -> list[dict]:
    items = payload.get("items") or payload.get("line_items")
    if isinstance(items, list) and items:
        return items

    raw_text = str(payload.get("raw_text") or "")
    if not raw_text:
        return []

    fallback_items = []
    skip_prefixes = (
        "total", "bayar", "kasir", "tanggal", "transaksi", "terima kasih",
        "thank you", "subtotal", "diskon", "ppn", "kembalian", "cash",
        "alfamart", "indomaret", "receipt", "member", "promo", "alamat",
        "store", "no.", "nomor",
    )
    for line in raw_text.splitlines():
        cleaned = re.sub(r"\s+", " ", line.strip())
        if not cleaned:
            continue
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        if cleaned.lower().startswith(skip_prefixes):
            continue
        if not any(char.isdigit() for char in cleaned):
            continue
        patterns = [
            r"^(?P<name>.+?)\s+(?P<qty>\d+)\s*(?:x|X|@|\*)\s*(?P<price>\d+(?:[.,]\d{1,2})?)$",
            r"^(?P<name>.+?)\s+(?P<qty>\d+)\s+(?P<price>\d+(?:[.,]\d{1,2})?)$",
            r"^(?P<name>.+?)\s+(?P<price>\d+(?:[.,]\d{1,2})?)$",
        ]
        match = None
        for pattern in patterns:
            match = re.match(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                break
        if match:
            name = match.group("name").strip(" -:|")
            qty = match.groupdict().get("qty")
            price = match.groupdict().get("price")
            quantity = normalize_integer_token(qty) if qty else 1
            normalized_price = normalize_numeric_token(price or "0")
            if name and normalized_price is not None and normalized_price >= 0:
                fallback_items.append({"item": name, "quantity": quantity or 1, "price": normalized_price})
    return fallback_items


def validate_ocr_output(payload: dict) -> dict:
    """Validate and normalize OCR output. Handles both new accounting schema and legacy."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="OCR output harus berupa objek JSON.")

    # Detect new accounting schema and convert
    if "document_metadata" in payload or "line_items" in payload:
        payload = _convert_new_schema_to_legacy(payload)

    items = payload.get("items")
    if not isinstance(items, list) or len(items) == 0:
        items = fallback_parse_items(payload)
    if not isinstance(items, list) or len(items) == 0:
        raise HTTPException(status_code=400, detail="OCR output harus berisi minimal 1 item transaksi.")

    normalized_items = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"Item ke-{index} tidak valid.")
        item_name = str(item.get("item") or item.get("description") or item.get("name") or "").strip() or f"item_{index}"
        qty_val = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
        price_val = item.get("price") if item.get("price") is not None else (item.get("line_total_net") or item.get("amount"))
        quantity_int = normalize_integer_token(qty_val) if qty_val is not None else 1
        price_float = normalize_numeric_token(price_val) if price_val is not None else 0.0
        if quantity_int is None or quantity_int < 1:
            quantity_int = 1
        if price_float is None or price_float < 0:
            price_float = 0.0

        normalized_item = {"item": item_name, "quantity": quantity_int, "price": price_float}
        # Preserve accounting fields if present
        for extra_key in ("item_code", "unit", "unit_price_effective", "discount_amount", "line_total_net", "account_mapping"):
            if extra_key in item:
                normalized_item[extra_key] = item[extra_key]
        normalized_items.append(normalized_item)

    total_amount = payload.get("total_amount")
    if total_amount is None:
        total_amount = sum(i["price"] * i["quantity"] for i in normalized_items)
    else:
        total_amount = normalize_numeric_token(total_amount) or 0.0

    result = {**payload, "items": normalized_items, "total_amount": total_amount, "currency": payload.get("currency") or "IDR"}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class TransactionItem(BaseModel):
    item: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    price: float = Field(..., ge=0)


class SaveTransactionRequest(BaseModel):
    source: str = Field(default="whatsapp")
    receipt_filename: Optional[str] = None
    items: Optional[List[TransactionItem]] = None
    total_amount: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    merchant_name: Optional[str] = None
    transaction_date: Optional[str] = None
    payment_method: Optional[str] = None
    currency: Optional[str] = "IDR"
    receipt_data: Optional[dict] = None
    type: Optional[str] = "expense"
    category: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None


class RegisterUserRequest(BaseModel):
    phone_number: str = Field(..., description="WhatsApp phone number (E.164 format, e.g. 628123456789)")
    name: Optional[str] = None
    role: str = Field(default="employee", description="'owner' or 'employee'")
    tenant_id: str = Field(..., description="The tenant (business) ID this user belongs to")


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def read_root():
    return {"message": "AGORA AI Engine is running.", "version": "1.0.0"}


# ─────────────────────────────────────────────────────────────────────────────
# Routes — WhatsApp Webhook
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/webhook", tags=["WhatsApp"])
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta WhatsApp Webhook Verification (GET).
    Meta sends this request when you configure the webhook URL in the developer portal.
    Must return hub.challenge as plain integer to confirm ownership.
    """
    from fastapi.responses import PlainTextResponse
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        print(f"[WEBHOOK] Verification successful.")
        return PlainTextResponse(content=str(hub_challenge))
    raise HTTPException(status_code=403, detail="Webhook verification failed — invalid verify token.")


@app.post("/webhook", tags=["WhatsApp"])
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Meta WhatsApp Cloud API Webhook receiver (POST).

    Immediately returns 200 OK to Meta, then processes the message
    asynchronously in the background via BackgroundTasks.
    """
    try:
        body = await request.json()
    except Exception:
        # Meta sometimes sends empty pings; always return 200
        return {"status": "ok"}

    # Navigate the Meta payload structure
    # Structure: body.entry[].changes[].value.messages[]
    try:
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    # Schedule background processing — non-blocking
                    background_tasks.add_task(_run_async_task, message)
    except Exception as e:
        print(f"[WEBHOOK] Error parsing payload: {e}")

    # CRITICAL: Always return 200 immediately so Meta doesn't retry
    return {"status": "ok"}


def _run_async_task(message_data: dict):
    """
    Wrapper to run the async background task from a sync BackgroundTasks context.

    NOTE: asyncio.run() raises RuntimeError when called from inside an already-running
    event loop (FastAPI/uvicorn). We spawn a new thread with its own event loop instead.
    """
    import threading
    from tasks import process_webhook_message

    def run_in_thread():
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(process_webhook_message(message_data))
        finally:
            loop.close()

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# Routes — OCR / Receipt Extraction
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/extract-receipt", tags=["AI"])
def extract_receipt(file: UploadFile = File(...)):
    """
    Extracts structured financial data from a receipt/nota image.
    Uses Gemini 1.5 Flash Vision for efficient processing.
    """
    file_bytes = file.file.read()
    base64_image = base64.b64encode(file_bytes).decode("utf-8")

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise HTTPException(
            status_code=500,
            detail="Tidak ada AI provider yang tersedia (GOOGLE_API_KEY kosong).",
        )
    try:
        extracted_text = _call_gemini_ocr(base64_image, file.content_type or "image/jpeg")
        normalized_data = normalize_ocr_payload(extracted_text)
        validated_ocr = validate_ocr_output(normalized_data)
        print(f"[OCR] Gemini success: {file.filename}")
        return {"filename": file.filename, "status": "success", "provider": "gemini", "data": validated_ocr}
    except HTTPException:
        raise
    except Exception as gemini_err:
        print(f"[OCR] Gemini failed: {gemini_err}")
        raise HTTPException(status_code=500, detail=f"OCR provider gagal: {gemini_err}")


def _call_gemini_ocr(base64_image: str, content_type: str) -> str:
    """
    OCR using Gemini Flash Vision with Certified Financial Accountant protocol.
    Includes retry logic, model fallback, document intelligence, and double-entry accounting.
    """
    import time
    from google import genai as gai
    from google.genai import types as gai_types

    client = gai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    prompt = """Anda adalah "Certified Financial Accountant & Document Intelligence Specialist".
Tugas: Ekstrak, verifikasi matematis, klasifikasikan, dan bukukan dokumen keuangan dari gambar ini.

PENTING: Gambar mungkin miring/terputar 90 derajat. Sesuaikan arah baca dengan teliti.

## PROTOKOL PARSING DOKUMEN
1. Identifikasi Jenis Dokumen:
   - FAKTUR_KREDIT: Transaksi akrual (menimbulkan Utang/Piutang Usaha)
   - NOTA_KONTAN / STRUK: Transaksi kas langsung
   - DELIVERY_ORDER: Bukti fisik barang saja
   - KUITANSI: Bukti pembayaran/pelunasan

2. Diferensiasi Harga:
   - JANGAN ambil harga master/karton jika beli eceran (TGH, KCL, Pcs)
   - Cari kolom Total/Netto/Subtotal per baris sebagai nilai akhir
   - Jika unit turunan: Subtotal = (Qty Beli / Isi Master) × Harga Net Master

3. Ekstraksi Pajak & Diskon:
   - Pisahkan diskon per item vs diskon faktur (bottom-line)
   - Periksa PPN: include atau exclude

## VALIDASI MATEMATIS (3 LAPIS)
1. Row Check: Qty × Harga Efektif - Diskon Baris = Subtotal Baris
2. Grand Total Check: Σ Subtotal - Diskon Faktur + PPN + Biaya Lain = Grand Total
3. Jika selisih ≤ Rp 100: masukkan ke Selisih Pembulatan
   Jika selisih > Rp 100: set "is_math_verified": false

## CHART OF ACCOUNTS
Aset: 1-1001 Kas, 1-1002 Bank, 1-1020 Piutang Usaha, 1-1030 Persediaan Barang Dagang,
      1-1031 Persediaan Makanan & Minuman, 1-1032 Persediaan ATK & Perlengkapan
Liabilitas: 2-1010 Utang Usaha, 2-1020 Utang PPN
Pendapatan: 4-1001 Pendapatan Penjualan
Beban: 5-1001 HPP, 6-1010 Beban Konsumsi, 6-1020 Beban Operasional, 6-9999 Selisih Pembulatan

Aturan Jurnal: Total DEBIT = Total KREDIT.
- Beli tunai: Debit Persediaan/Beban, Kredit Kas
- Beli tempo/faktur: Debit Persediaan, Kredit Utang Usaha

## FORMAT OUTPUT (JSON MURNI, TANPA TEKS LAIN)
{
  "document_metadata": {
    "document_type": "FAKTUR_KREDIT | NOTA_KONTAN | DELIVERY_ORDER | STRUK",
    "invoice_number": "string | null",
    "transaction_date": "YYYY-MM-DD | null",
    "due_date": "YYYY-MM-DD | null",
    "vendor": {"name": "string", "tax_id_npwp": "string | null", "address": "string | null"},
    "customer": {"name": "string | null", "customer_id": "string | null"}
  },
  "line_items": [
    {
      "item_code": "string | null",
      "description": "string",
      "quantity": 0,
      "unit": "string",
      "unit_price_effective": 0,
      "discount_amount": 0,
      "line_total_net": 0,
      "account_mapping": "string"
    }
  ],
  "financial_summary": {
    "subtotal": 0,
    "total_discount": 0,
    "tax_ppn": 0,
    "grand_total": 0,
    "is_math_verified": true,
    "math_discrepancy_amount": 0
  },
  "accounting_entries": [
    {"account_code": "string", "account_name": "string", "debit": 0, "credit": 0}
  ]
}

Jangan tambahkan teks apapun sebelum atau sesudah JSON."""

    image_data = gai_types.Part.from_bytes(
        data=base64.b64decode(base64_image),
        mime_type=content_type,
    )

    # Try multiple models in order — if one is overloaded, try the next
    models_to_try = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    last_error = None

    for model_name in models_to_try:
        # Each model gets up to 2 retries with backoff
        for attempt in range(2):
            try:
                print(f"[OCR] Trying {model_name} (attempt {attempt + 1})...")
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, image_data],
                )
                print(f"[OCR] ✅ Success with {model_name}")
                return resp.text.strip()
            except Exception as e:
                last_error = e
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait_time = (attempt + 1) * 3  # 3s, 6s
                    print(f"[OCR] ⚠️ {model_name} unavailable (attempt {attempt + 1}), waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    # Non-retryable error, raise immediately
                    raise
        print(f"[OCR] ❌ {model_name} failed after retries, trying next model...")

    # All models failed
    raise RuntimeError(f"Semua model Gemini gagal setelah retry: {last_error}")



# ─────────────────────────────────────────────────────────────────────────────
# Routes — Transactions (Manual / Raw)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/transactions", tags=["Transactions"])
def get_all_transactions(db: Session = Depends(get_db)):
    """Returns all transactions (no tenant filter). For debugging only."""
    transactions = db.query(models.Transaction).order_by(models.Transaction.created_at.desc()).all()
    return {"status": "success", "count": len(transactions), "transactions": transactions}


@app.post("/transactions", tags=["Transactions"])
def save_transaction(payload: SaveTransactionRequest, db: Session = Depends(get_db)):
    """Manually save a transaction record (bypasses agent pipeline)."""
    try:
        receipt_data = payload.receipt_data or {}

        # Normalize items
        if payload.items:
            normalized_items = [item.model_dump() for item in payload.items]
        else:
            raw_items = receipt_data.get("items", [])
            normalized_items = []
            for item in raw_items:
                if isinstance(item, dict):
                    normalized_items.append({
                        "item": str(item.get("item") or item.get("name") or "Unknown"),
                        "quantity": int(item.get("quantity") or item.get("qty") or 1),
                        "price": float(item.get("price") or item.get("amount") or 0),
                    })

        if not normalized_items:
            raise HTTPException(status_code=400, detail="Payload harus berisi field items yang valid.")

        total_amount = payload.total_amount or float(
            sum(i["price"] * i["quantity"] for i in normalized_items)
        )

        # Resolve tenant
        tenant_id = payload.tenant_id or "default-tenant"
        tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
        if not tenant:
            tenant = models.Tenant(id=tenant_id, name="Default Tenant")
            db.add(tenant)
            db.commit()

        txn_date = None
        txn_date_str = payload.transaction_date or receipt_data.get("transaction_date")
        if txn_date_str:
            try:
                txn_date = datetime.fromisoformat(txn_date_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        transaction_id = str(uuid.uuid4())
        db_transaction = models.Transaction(
            id=transaction_id,
            tenant_id=tenant_id,
            user_id=payload.user_id,
            source=payload.source,
            receipt_filename=payload.receipt_filename,
            items=normalized_items,
            total_amount=total_amount,
            notes=payload.notes,
            merchant_name=payload.merchant_name or receipt_data.get("merchant_name"),
            transaction_date=txn_date,
            payment_method=payload.payment_method or receipt_data.get("payment_method"),
            currency=payload.currency or receipt_data.get("currency") or "IDR",
            type=payload.type or "expense",
            category=payload.category,
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        return {
            "status": "success",
            "message": "Transaction berhasil disimpan.",
            "transaction_id": transaction_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan transaksi: {str(exc)}")


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dashboard/summary", tags=["Dashboard"])
def get_dashboard_summary(
    tenant_id: str = Query("default-tenant", description="Tenant (business) ID"),
    user_id: Optional[str] = Query(None, description="Filter by specific user (phone number)"),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    Returns financial summary for a tenant (optionally filtered by user):
    - Total pemasukan (income)
    - Total pengeluaran (expense)
    - Net profit/loss
    - Breakdown by category
    """
    # Resolve tenant name
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    tenant_name = tenant.name if tenant else tenant_id

    query = db.query(models.Transaction).filter(models.Transaction.tenant_id == tenant_id)

    # Filter by user_id if provided
    if user_id:
        query = query.filter(models.Transaction.user_id == user_id)

    if date_from:
        try:
            query = query.filter(
                models.Transaction.created_at >= datetime.fromisoformat(date_from)
            )
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(
                models.Transaction.created_at <= datetime.fromisoformat(date_to + "T23:59:59")
            )
        except ValueError:
            pass

    transactions = query.all()

    total_income = sum(t.total_amount for t in transactions if t.type == "income")
    total_expense = sum(t.total_amount for t in transactions if t.type == "expense")
    net = total_income - total_expense

    # Category breakdown
    category_breakdown: dict[str, dict] = {}
    for t in transactions:
        cat = t.category or "Lain-lain"
        if cat not in category_breakdown:
            category_breakdown[cat] = {"income": 0.0, "expense": 0.0, "count": 0}
        if t.type == "income":
            category_breakdown[cat]["income"] += t.total_amount
        else:
            category_breakdown[cat]["expense"] += t.total_amount
        category_breakdown[cat]["count"] += 1

    return {
        "status": "success",
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "user_id": user_id,
        "period": {"from": date_from, "to": date_to},
        "summary": {
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "net_profit": round(net, 2),
            "transaction_count": len(transactions),
        },
        "category_breakdown": category_breakdown,
    }


@app.get("/dashboard/transactions", tags=["Dashboard"])
def get_dashboard_transactions(
    tenant_id: str = Query("default-tenant"),
    user_id: Optional[str] = Query(None, description="Filter by specific user (phone number)"),
    type: Optional[str] = Query(None, description="Filter by type: 'income' or 'expense'"),
    category: Optional[str] = Query(None, description="Filter by category"),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Returns paginated, filtered transaction list for the web dashboard.
    Supports filtering by type, category, and date range.
    """
    query = db.query(models.Transaction).filter(models.Transaction.tenant_id == tenant_id)

    # Filter by user_id if provided
    if user_id:
        query = query.filter(models.Transaction.user_id == user_id)

    if type:
        query = query.filter(models.Transaction.type == type)
    if category:
        query = query.filter(models.Transaction.category == category)
    if date_from:
        try:
            query = query.filter(
                models.Transaction.created_at >= datetime.fromisoformat(date_from)
            )
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(
                models.Transaction.created_at <= datetime.fromisoformat(date_to + "T23:59:59")
            )
        except ValueError:
            pass

    total_count = query.count()
    transactions = (
        query.order_by(models.Transaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "status": "success",
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size,
        },
        "transactions": [
            {
                "id": t.id,
                "type": t.type,
                "category": t.category,
                "total_amount": t.total_amount,
                "currency": t.currency,
                "merchant_name": t.merchant_name,
                "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                "payment_method": t.payment_method,
                "items": t.items,
                "notes": t.notes,
                "source": t.source,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transactions
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes — User Management
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/users", tags=["User Management"])
def register_user(payload: RegisterUserRequest, db: Session = Depends(get_db)):
    """
    Register a new user (owner or employee) for a tenant.
    The phone_number is used as the WhatsApp sender ID.
    """
    # Ensure tenant exists
    tenant = db.query(models.Tenant).filter(models.Tenant.id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail=f"Tenant '{payload.tenant_id}' tidak ditemukan. Buat tenant terlebih dahulu.",
        )

    existing = db.query(models.User).filter(models.User.id == payload.phone_number).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Nomor {payload.phone_number} sudah terdaftar.",
        )

    if payload.role not in ("owner", "employee"):
        raise HTTPException(status_code=400, detail="Role harus 'owner' atau 'employee'.")

    user = models.User(
        id=payload.phone_number,
        tenant_id=payload.tenant_id,
        role=payload.role,
        name=payload.name,
    )
    db.add(user)
    db.commit()

    return {
        "status": "success",
        "message": f"User {payload.phone_number} berhasil didaftarkan sebagai {payload.role}.",
        "user": {
            "phone_number": user.id,
            "name": user.name,
            "role": user.role,
            "tenant_id": user.tenant_id,
        },
    }


@app.get("/tenants/{tenant_id}/users", tags=["User Management"])
def list_tenant_users(tenant_id: str, db: Session = Depends(get_db)):
    """List all registered users (owners + employees) for a tenant."""
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' tidak ditemukan.")

    users = db.query(models.User).filter(models.User.tenant_id == tenant_id).all()
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "users": [
            {"phone_number": u.id, "name": u.name, "role": u.role}
            for u in users
        ],
    }


@app.get("/dashboard/ui/{tenant_id}", tags=["Dashboard"], response_class=HTMLResponse)
def dashboard_ui(tenant_id: str, db: Session = Depends(get_db)):
    """Menampilkan visual dashboard (HTML) untuk seluruh tenant."""
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant tidak ditemukan.")
    
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Dashboard UI file not found.")
        
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    return HTMLResponse(content=html_content)


@app.get("/dashboard/ui/{tenant_id}/{user_id}", tags=["Dashboard"], response_class=HTMLResponse)
def dashboard_ui_per_user(tenant_id: str, user_id: str, db: Session = Depends(get_db)):
    """Menampilkan visual dashboard (HTML) untuk satu user spesifik."""
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant tidak ditemukan.")
    
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Dashboard UI file not found.")
        
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    return HTMLResponse(content=html_content)
