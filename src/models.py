from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, Text, JSON, Boolean
from sqlalchemy.sql import func
from database import Base

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True) # Nomor WhatsApp
    tenant_id = Column(String, ForeignKey("tenants.id"))
    name = Column(String, nullable=True)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"))
    source = Column(String, default="whatsapp")
    receipt_filename = Column(String, nullable=True)
    items = Column(JSON, nullable=False) # daftar item
    total_amount = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    merchant_name = Column(String, nullable=True)
    transaction_date = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)
    currency = Column(String, default="IDR")
    type = Column(String, default="expense") # 'income' (pemasukan) atau 'expense' (pengeluaran)
    category = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Kolom Akuntansi
    document_type = Column(String, nullable=True)        # FAKTUR_KREDIT, NOTA_KONTAN, DELIVERY_ORDER, STRUK
    invoice_number = Column(String, nullable=True)       # Nomor faktur/nota
    vendor_name = Column(String, nullable=True)          # Nama vendor/supplier
    tax_ppn = Column(Float, default=0)                   # Jumlah PPN
    discount_total = Column(Float, default=0)            # Total diskon
    accounting_entries = Column(JSON, nullable=True)      # [{account_code, account_name, debit, credit}]
    is_math_verified = Column(Boolean, default=True)     # Status validasi matematis
    math_discrepancy = Column(Float, default=0)          # Selisih pembulatan

