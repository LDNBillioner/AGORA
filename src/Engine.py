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

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"  # for LLM models
NVIDIA_OCR_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2"  # dedicated OCR endpoint
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
    cleaned_text = re.sub(r'```(?:json)?\n?|```', '', extracted_text).strip()
    try:
        parsed = json.loads(cleaned_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"raw_text": extracted_text, "items": [], "total_amount": None, "currency": "IDR"}


def fallback_parse_items(payload: dict) -> list[dict]:
    items = payload.get("items")
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
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="OCR output harus berupa objek JSON.")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) == 0:
        items = fallback_parse_items(payload)
    if not isinstance(items, list) or len(items) == 0:
        raise HTTPException(status_code=400, detail="OCR output harus berisi minimal 1 item transaksi.")
    normalized_items = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"Item ke-{index} tidak valid.")
        item_name = str(item.get("item") or item.get("name") or "").strip() or f"item_{index}"
        qty_val = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
        price_val = item.get("price") if item.get("price") is not None else item.get("amount")
        quantity_int = normalize_integer_token(qty_val) if qty_val is not None else 1
        price_float = normalize_numeric_token(price_val) if price_val is not None else 0.0
        if quantity_int is None or quantity_int < 1:
            quantity_int = 1
        if price_float is None or price_float < 0:
            price_float = 0.0
        normalized_items.append({"item": item_name, "quantity": quantity_int, "price": price_float})
    total_amount = payload.get("total_amount")
    if total_amount is None:
        total_amount = sum(i["price"] * i["quantity"] for i in normalized_items)
    else:
        total_amount = normalize_numeric_token(total_amount) or 0.0
    return {**payload, "items": normalized_items, "total_amount": total_amount, "currency": payload.get("currency") or "IDR"}


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

    Pipeline:
    1. Primary  — NVIDIA Nemotron-OCR-v2 (ai.api.nvidia.com/v1/cv/...)
       Uses dedicated CV endpoint, NOT the chat/completions endpoint.
    2. Fallback — Gemini 1.5 Flash Vision (if NVIDIA fails or key missing).
    """
    file_bytes = file.file.read()
    base64_image = base64.b64encode(file_bytes).decode("utf-8")

    # ── 1. Try NVIDIA Nemotron-OCR-v2 ────────────────────────────────────────
    if NVIDIA_API_KEY:
        try:
            extracted_text = _call_nvidia_ocr(base64_image, file.content_type or "image/jpeg")
            normalized_data = normalize_ocr_payload(extracted_text)
            validated_ocr = validate_ocr_output(normalized_data)
            print(f"[OCR] NVIDIA success: {file.filename}")
            return {"filename": file.filename, "status": "success", "provider": "nvidia", "data": validated_ocr}
        except HTTPException:
            raise
        except Exception as nvidia_err:
            print(f"[OCR] NVIDIA failed ({nvidia_err}), trying Gemini fallback...")

    # ── 2. Fallback: Gemini Vision ────────────────────────────────────────────
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise HTTPException(
            status_code=500,
            detail="Tidak ada AI provider yang tersedia (NVIDIA_API_KEY & GOOGLE_API_KEY kosong).",
        )
    try:
        extracted_text = _call_gemini_ocr(base64_image, file.content_type or "image/jpeg")
        normalized_data = normalize_ocr_payload(extracted_text)
        validated_ocr = validate_ocr_output(normalized_data)
        print(f"[OCR] Gemini fallback success: {file.filename}")
        return {"filename": file.filename, "status": "success", "provider": "gemini-fallback", "data": validated_ocr}
    except HTTPException:
        raise
    except Exception as gemini_err:
        print(f"[OCR] Gemini fallback also failed: {gemini_err}")
        raise HTTPException(status_code=500, detail=f"Semua OCR provider gagal: {gemini_err}")


def _call_nvidia_ocr(base64_image: str, content_type: str) -> str:
    """
    Calls NVIDIA Nemotron-OCR-v2 via its dedicated CV endpoint.
    Endpoint: POST https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2
    Payload:  { "image": "<base64>", "render_mmcontent": false }
    """
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # Nemotron-OCR-v2 dedicated payload format (NOT chat/completions)
    # 'input' is a list; each item needs 'url' field — confirmed from 422 error
    payload = {
        "input": [
            {"url": f"data:{content_type};base64,{base64_image}"}
        ],
        "render_mmcontent": False,
    }

    with httpx.Client() as http_client:
        response = http_client.post(NVIDIA_OCR_URL, headers=headers, json=payload, timeout=60.0)

    if response.status_code != 200:
        raise RuntimeError(f"NVIDIA OCR HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()

    # Response may be { "text": "..." } or { "choices": [{...}] }
    raw_text = (
        data.get("text")
        or data.get("content")
        or (data.get("choices", [{}])[0].get("message", {}).get("content"))
        or json.dumps(data)
    )

    # Build an OCR-style prompt context and call Gemini to convert raw text → JSON schema
    # (Nemotron-OCR returns raw markdown text, not structured JSON)
    return _nvidia_text_to_json_schema(str(raw_text))


def _nvidia_text_to_json_schema(raw_ocr_text: str) -> str:
    """
    Nemotron-OCR-v2 returns raw extracted text (markdown/plain).
    Use Gemini to convert it to the required JSON schema.
    """
    from google import genai as gai
    from google.genai import types as gai_types
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return json.dumps({"raw_text": raw_ocr_text})
    client = gai.Client(api_key=google_api_key)
    prompt = (
        "Berikut adalah teks hasil OCR dari struk/nota. "
        "Konversikan ke JSON murni dengan schema berikut (tanpa penjelasan, hanya JSON):\n"
        '{"merchant_name": string, "transaction_date": string|null, '
        '"items": [{"item": string, "quantity": integer, "price": number}], '
        '"total_amount": number, "payment_method": string|null, "currency": "IDR"}\n\n'
        f"Teks OCR:\n{raw_ocr_text}"
    )
    resp = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
    return resp.text.strip()


def _call_gemini_ocr(base64_image: str, content_type: str) -> str:
    """
    Fallback OCR using Gemini 2.0 Flash Vision (new google.genai SDK).
    Sends image directly with structured extraction prompt.
    """
    from google import genai as gai
    from google.genai import types as gai_types
    client = gai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    prompt = (
        "Ekstrak data dari gambar struk/nota ini dan kembalikan JSON murni yang valid. "
        'Schema wajib: {"merchant_name": string, "transaction_date": string|null, '
        '"items": [{"item": string, "quantity": integer, "price": number}], '
        '"total_amount": number, "payment_method": string|null, "currency": "IDR"}. '
        "Aturan: price harus angka numerik tanpa simbol, quantity harus integer, "
        "jangan tambahkan teks apa pun sebelum atau sesudah JSON."
    )
    image_data = gai_types.Part.from_bytes(
        data=base64.b64decode(base64_image),
        mime_type=content_type,
    )
    resp = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=[prompt, image_data],
    )
    return resp.text.strip()



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
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    requester_phone: Optional[str] = Query(None, description="Phone number of the requester (for RBAC)"),
    db: Session = Depends(get_db),
):
    """
    Returns financial summary for a tenant:
    - Total pemasukan (income)
    - Total pengeluaran (expense)
    - Net profit/loss
    - Breakdown by category

    RBAC: Only 'owner' role can access this endpoint.
    """
    # RBAC: block employee from seeing financial summary
    if requester_phone:
        requester = db.query(models.User).filter(models.User.id == requester_phone).first()
        if requester and requester.role == "employee":
            raise HTTPException(
                status_code=403,
                detail="Akses ditolak. Karyawan tidak memiliki izin untuk melihat ringkasan keuangan.",
            )

    query = db.query(models.Transaction).filter(models.Transaction.tenant_id == tenant_id)

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
    type: Optional[str] = Query(None, description="Filter by type: 'income' or 'expense'"),
    category: Optional[str] = Query(None, description="Filter by category"),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    requester_phone: Optional[str] = Query(None, description="Phone number of the requester (for RBAC)"),
    db: Session = Depends(get_db),
):
    """
    Returns paginated, filtered transaction list for the web dashboard.
    Supports filtering by type, category, and date range.

    RBAC: Only 'owner' role can access this endpoint.
    """
    # RBAC: block employee from seeing full transaction list
    if requester_phone:
        requester = db.query(models.User).filter(models.User.id == requester_phone).first()
        if requester and requester.role == "employee":
            raise HTTPException(
                status_code=403,
                detail="Akses ditolak. Karyawan tidak memiliki izin untuk melihat daftar transaksi.",
            )

    query = db.query(models.Transaction).filter(models.Transaction.tenant_id == tenant_id)

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
