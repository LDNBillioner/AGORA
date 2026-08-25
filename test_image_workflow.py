import sys
import os
import base64
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))
load_dotenv()

from agent import process_message  # type: ignore
from Engine import _call_gemini_ocr, normalize_ocr_payload, validate_ocr_output  # type: ignore
from database import SessionLocal  # type: ignore
from tasks import get_or_create_user  # type: ignore

def mock_extract_receipt_text(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Mock of tasks.extract_receipt_text that bypasses HTTP to call Engine directly."""
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    extracted_text = _call_gemini_ocr(base64_image, mime_type)
    normalized_data = normalize_ocr_payload(extracted_text)
    validated_ocr = validate_ocr_output(normalized_data)
    
    ocr_data = validated_ocr
    items = ocr_data.get("items", [])
    total = ocr_data.get("total_amount", 0)
    merchant = ocr_data.get("merchant_name", "")
    date = ocr_data.get("transaction_date", "")
    payment = ocr_data.get("payment_method", "")
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


def run_image_test(image_paths):
    for image_path in image_paths:
        print(f"\n=======================================================")
        print(f"PROCESSING IMAGE: {os.path.basename(image_path)}")
        print(f"=======================================================")
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        print("--- 1. Extracting Text from Image (OCR) ---")
        agent_input_text = mock_extract_receipt_text(image_bytes, "image/jpeg")
        print("\nOCR OUTPUT:")
        print(agent_input_text)
        
        tenant_id = "test-tenant-1"
        user_id = "test-user-1"
        rag_context = "Belum ada riwayat transaksi."
        
        # Ensure user exists
        db = SessionLocal()
        try:
            get_or_create_user(db, user_id)
        finally:
            db.close()
        
        print("\n--- 2. Invoking AI Agent ---")
        result = process_message(
            tenant_id=tenant_id,
            user_id=user_id,
            message=agent_input_text,
            rag_context=rag_context
        )
        
        print("\nFINAL RESULT FROM LANGGRAPH AGENT:")
        import pprint
        pprint.pprint(result)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        paths = ["/home/sh1zu/.gemini/antigravity-ide/brain/dcc44182-1a76-4532-8e75-1a7295902093/media__1787649604608.jpg"]
    run_image_test(paths)
