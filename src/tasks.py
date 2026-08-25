"""
tasks.py — Async background processor for WhatsApp webhook messages.

Handles multi-modal routing:
  - text  → agent
  - audio → Gemini 1.5 Flash (multimodal audio) → agent
  - image → NVIDIA Nemotron-OCR → format → agent"""

import os
import io
import asyncio
import httpx

from database import SessionLocal
import models
from rag import retrieve_past_transactions
from agent import process_message

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_API_VERSION = "v20.0"
AGORA_ENGINE_URL = os.getenv("AGORA_ENGINE_URL", "http://localhost:8000")

ONBOARDING_MESSAGE = (
    "👋 Halo! Selamat datang di *AGORA AI Accountant*!\n\n"
    "Saya siap membantu mencatat keuangan bisnis kamu langsung dari WhatsApp.\n\n"
    "Cukup kirim pesan seperti:\n"
    "• 📝 *Teks:* \"Jual 5 kopi susu @15rb\"\n"
    "• 🎙️ *Voice Note:* Rekam nominal transaksi kamu\n"
    "• 🧾 *Foto Struk:* Foto nota/struk belanja langsung\n\n"
    "Saya akan otomatis mencatatnya ke dashboard keuangan kamu. 📊\n\n"
    "Ayo mulai catat transaksi pertama kamu! 🚀"
)


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp API Helpers
# ─────────────────────────────────────────────────────────────────────────────

def send_whatsapp_message(to_number: str, message: str):
    """Sends a text message via Meta WhatsApp Cloud API (sync)."""
    url = (
        f"https://graph.facebook.com/{META_API_VERSION}"
        f"/{META_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message},
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        print(f"[WA] Message sent to {to_number}: {message[:60]}...")
    except Exception as e:
        print(f"[WA] Failed to send message to {to_number}: {e}")


async def download_media_bytes(media_id: str) -> bytes:
    """
    Downloads a WhatsApp media file (image or audio) by its media_id.
    Step 1: Get the media URL from Meta API.
    Step 2: Download the actual binary content.
    """
    meta_url = f"https://graph.facebook.com/{META_API_VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Resolve media URL
        url_resp = await client.get(meta_url, headers=headers)
        url_resp.raise_for_status()
        media_url = url_resp.json().get("url")
        if not media_url:
            raise ValueError(f"Could not resolve media URL for id={media_id}")

        # Step 2: Download binary content
        media_resp = await client.get(media_url, headers=headers)
        media_resp.raise_for_status()
        return media_resp.content


# ─────────────────────────────────────────────────────────────────────────────
# Audio: Gemini Multimodal STT
# ─────────────────────────────────────────────────────────────────────────────

async def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Transcribes audio bytes using Google Gemini 3.5 Flash multimodal capability.
    No separate STT API needed — uses the same GOOGLE_API_KEY as the agent.
    Uses the new google.genai SDK (not the deprecated google.generativeai).
    Returns the transcribed text string.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    audio_part = types.Part.from_bytes(
        data=audio_bytes,
        mime_type=mime_type,
    )
    prompt = (
        "Transkrip isi audio berikut dalam Bahasa Indonesia. "
        "Kembalikan teks transkrip saja, tanpa penjelasan tambahan."
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-2.5-flash",
        contents=[prompt, audio_part],
    )
    return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Image: OCR via Gemini Vision
# ─────────────────────────────────────────────────────────────────────────────

async def extract_receipt_text(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """
    Sends image bytes to the local /extract-receipt endpoint (Gemini Vision OCR).
    Returns a formatted string with full accounting data for the agent.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        files = {"file": ("receipt.jpg", io.BytesIO(image_bytes), mime_type)}
        response = await client.post(f"{AGORA_ENGINE_URL}/extract-receipt", files=files)
        response.raise_for_status()
        ocr_data = response.json().get("data", {})

    # Format OCR output into a comprehensive accounting description for the agent
    items = ocr_data.get("items", [])
    total = ocr_data.get("total_amount", 0)
    merchant = ocr_data.get("merchant_name", "")
    date = ocr_data.get("transaction_date", "")
    payment = ocr_data.get("payment_method", "")

    # Accounting-specific data
    doc_type = ocr_data.get("document_type", "STRUK")
    invoice_num = ocr_data.get("invoice_number", "")
    vendor = ocr_data.get("vendor_name", "") or merchant
    tax_ppn = ocr_data.get("tax_ppn", 0)
    discount = ocr_data.get("discount_total", 0)
    is_verified = ocr_data.get("is_math_verified", True)
    math_disc = ocr_data.get("math_discrepancy", 0)
    acct_entries = ocr_data.get("accounting_entries", [])

    lines = ["[DOKUMEN KEUANGAN TERDETEKSI — Data OCR + Akuntansi:]"]
    lines.append(f"Jenis Dokumen: {doc_type}")
    if invoice_num:
        lines.append(f"No. Faktur/Nota: {invoice_num}")
    if vendor:
        lines.append(f"Vendor/Supplier: {vendor}")
    if merchant and merchant != vendor:
        lines.append(f"Merchant: {merchant}")
    if date:
        lines.append(f"Tanggal: {date}")
    if payment:
        lines.append(f"Pembayaran: {payment}")

    lines.append("\nItem:")
    for item in items:
        item_name = item.get("item", "")
        qty = item.get("quantity", 1)
        price = item.get("price", 0)
        unit = item.get("unit", "pcs")
        lines.append(f"  - {item_name} x{qty} {unit} @ Rp {price:,.0f}")

    if discount:
        lines.append(f"\nTotal Diskon: Rp {discount:,.0f}")
    if tax_ppn:
        lines.append(f"PPN: Rp {tax_ppn:,.0f}")
    lines.append(f"Grand Total: Rp {total:,.0f}")

    if not is_verified:
        lines.append(f"\n⚠️ PERINGATAN: Validasi matematis GAGAL (selisih: Rp {math_disc:,.0f})")

    if acct_entries:
        lines.append("\n📒 Jurnal Akuntansi (Double-Entry):")
        for entry in acct_entries:
            code = entry.get("account_code", "")
            name = entry.get("account_name", "")
            debit = entry.get("debit", 0)
            credit = entry.get("credit", 0)
            if debit:
                lines.append(f"  Debit  {code} {name}: Rp {debit:,.0f}")
            if credit:
                lines.append(f"  Kredit {code} {name}: Rp {credit:,.0f}")

    lines.append(
        "\nTolong catat transaksi ini sesuai data di atas, termasuk "
        "document_type, invoice_number, vendor_name, tax_ppn, discount_total, "
        "accounting_entries, is_math_verified, dan math_discrepancy."
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# User / Tenant Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_user(db, sender_number: str) -> tuple[models.User, bool]:
    """
    Returns (user, is_new) — creates tenant + user automatically if not found.
    Everyone is grouped under default-tenant without roles.
    """
    user = db.query(models.User).filter(models.User.id == sender_number).first()
    if user:
        return user, False

    # Auto-create default tenant if none exists
    tenant = db.query(models.Tenant).first()
    if not tenant:
        tenant = models.Tenant(id="default-tenant", name="Default Tenant")
        db.add(tenant)
        db.commit()

    user = models.User(
        id=sender_number,
        tenant_id=tenant.id,
        name=None,
    )
    db.add(user)
    db.commit()
    return user, True


# ─────────────────────────────────────────────────────────────────────────────
# Main Background Task
# ─────────────────────────────────────────────────────────────────────────────

async def process_webhook_message(message_data: dict):
    """
    Background tasks for processing WhatsApp messages.
    Pipeline:
    - Text → AI Agent
    - Audio → Whisper/Gemini STT → Agent
    - Image → Gemini Vision OCR → Agent
    
    Always returns 200 immediately to Meta (called from webhook endpoint).
    """
    sender_number: str = message_data.get("from", "")
    msg_type: str = message_data.get("type", "text")

    if not sender_number:
        print("[TASK] No sender number, skipping.")
        return

    db = SessionLocal()
    try:
        # ── 1. User / Tenant resolution ──────────────────────────────────────
        user, is_new = get_or_create_user(db, sender_number)
        tenant_id = user.tenant_id

        if is_new:
            send_whatsapp_message(sender_number, ONBOARDING_MESSAGE)
            return  # New users get onboarding first, process next message

        # ── 2. Retrieve RAG context (50 past transactions) ───────────────────
        # Use text body if available, otherwise generic query for audio/image
        msg_body = message_data.get("text", {}).get("body", "")
        text_query = msg_body if msg_body else "transaksi terbaru"
        rag_context = retrieve_past_transactions(tenant_id, text_query, k=50)

        # ── 3. Multi-modal routing ───────────────────────────────────────────
        agent_input_text = None

        if msg_type == "text":
            # Plain text message
            agent_input_text = message_data.get("text", {}).get("body", "")
            if not agent_input_text:
                send_whatsapp_message(
                    sender_number,
                    "Maaf Kak, pesan teks tidak terbaca. Coba kirim ulang ya 🙏",
                )
                return

        elif msg_type == "audio":
            # Voice note → Whisper STT
            audio_id = message_data.get("audio", {}).get("id")
            audio_mime = message_data.get("audio", {}).get("mime_type", "audio/ogg")
            if not audio_id:
                send_whatsapp_message(
                    sender_number,
                    "Maaf Kak, voice note tidak terdeteksi. Coba kirim ulang ya 🙏",
                )
                return

            send_whatsapp_message(
                sender_number,
                "🎙️ Voice note diterima! Sedang mentranskrip audio kamu...",
            )
            try:
                audio_bytes = await download_media_bytes(audio_id)
                agent_input_text = await transcribe_audio(audio_bytes, audio_mime)
                print(f"[STT] Transcribed: {agent_input_text}")
            except Exception as stt_err:
                print(f"[STT] Error: {stt_err}")
                send_whatsapp_message(
                    sender_number,
                    "⚠️ Maaf Kak, gagal mentranskrip voice note. "
                    "Coba ketik manual atau kirim ulang ya 🙏",
                )
                return

        elif msg_type == "image":
            # Photo/receipt → Gemini OCR
            image_id = message_data.get("image", {}).get("id")
            image_mime = message_data.get("image", {}).get("mime_type", "image/jpeg")
            if not image_id:
                send_whatsapp_message(
                    sender_number,
                    "Maaf Kak, gambar tidak terdeteksi. Coba kirim ulang ya 🙏",
                )
                return

            send_whatsapp_message(
                sender_number,
                "🧾 Struk/nota diterima! Sedang membaca dengan Gemini Vision AI...",
            )
            try:
                image_bytes = await download_media_bytes(image_id)
                agent_input_text = await extract_receipt_text(image_bytes, image_mime)
                print(f"[OCR] Extracted: {agent_input_text[:200]}")
            except Exception as ocr_err:
                print(f"[OCR] Error: {ocr_err}")
                send_whatsapp_message(
                    sender_number,
                    "⚠️ Maaf Kak, gagal membaca struk. "
                    "Pastikan foto jelas dan coba lagi ya 🙏",
                )
                return

        else:
            # Unsupported message type
            send_whatsapp_message(
                sender_number,
                "Maaf Kak, saya hanya bisa memproses pesan teks, "
                "voice note 🎙️, dan foto struk 🧾.",
            )
            return

        # ── 4. Invoke Agent ──────────────────────────────────────────────────
        result = process_message(
            tenant_id=tenant_id,
            user_id=sender_number,
            message=agent_input_text,
            rag_context=rag_context,
        )

        reply = result.get("reply", "")
        if not reply:
            reply = "Maaf Kak, ada masalah di sistem kami. Coba lagi ya 🙏"

        # ── 5. Send reply back to user ───────────────────────────────────────
        send_whatsapp_message(sender_number, reply)

    except Exception as e:
        print(f"[TASK] Unhandled error processing webhook: {e}")
        send_whatsapp_message(
            sender_number,
            "Maaf Kak, terjadi kesalahan di sistem kami. "
            "Tim kami sudah diberitahu. Coba lagi dalam beberapa menit ya 🙏",
        )
    finally:
        db.close()
