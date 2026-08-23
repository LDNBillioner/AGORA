from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, Text, JSON
from sqlalchemy.sql import func
from database import Base

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True) # WhatsApp number
    tenant_id = Column(String, ForeignKey("tenants.id"))
    role = Column(String, default="employee") # 'owner' or 'employee'
    name = Column(String, nullable=True)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"))
    source = Column(String, default="whatsapp")
    receipt_filename = Column(String, nullable=True)
    items = Column(JSON, nullable=False) # list of items
    total_amount = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    merchant_name = Column(String, nullable=True)
    transaction_date = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)
    currency = Column(String, default="IDR")
    type = Column(String, default="expense") # 'income' or 'expense'
    category = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
