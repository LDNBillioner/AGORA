import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from agent import process_message
from database import SessionLocal
import models
from tasks import get_or_create_user

def run_test():
    tenant_id = "test-tenant-1"
    user_id = "test-user-1"
    message = "Bayar listrik 500rb"
    rag_context = "Belum ada riwayat transaksi."
    
    # Create the user to satisfy ForeignKey constraint
    db = SessionLocal()
    try:
        get_or_create_user(db, user_id)
    finally:
        db.close()
    
    print(f"Testing workflow with text: '{message}'")
    
    result = process_message(
        tenant_id=tenant_id,
        user_id=user_id,
        message=message,
        rag_context=rag_context
    )
    
    print("Result:")
    import pprint
    pprint.pprint(result)

if __name__ == "__main__":
    run_test()
