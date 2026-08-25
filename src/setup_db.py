"""
setup_db.py — One-click database setup for AGORA AI Accountant.

Usage:
    1. Pastikan DATABASE_URL di .env sudah diisi dengan URL Supabase.
    2. Jalankan: python3 setup_db.py

Script ini akan:
    1. Test koneksi ke database
    2. Enable ekstensi pgvector (untuk RAG)
    3. Buat semua tabel via Alembic migration
    4. Seed data awal (tenant + owner user)
"""

import os
import sys
import subprocess
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

def print_step(n, msg):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {msg}")
    print(f"{'='*60}")


def check_env():
    """Step 0: Validate DATABASE_URL is configured."""
    print_step(0, "Memeriksa konfigurasi .env")

    if not DATABASE_URL or "your_project_ref" in DATABASE_URL or "your_db_password" in DATABASE_URL:
        print("❌ ERROR: DATABASE_URL belum dikonfigurasi!")
        print()
        print("Buka file .env dan ganti DATABASE_URL dengan URL dari Supabase.")
        print("Contoh format:")
        print("  DATABASE_URL=postgresql://postgres.abcdefg:MyPassword123@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require")
        print()
        print("Cara mendapatkan URL:")
        print("  1. Buka Supabase Dashboard → Project Settings → Database")
        print("  2. Copy 'Connection String' (URI format)")
        print("  3. Pilih tipe 'Transaction' (port 6543) atau 'Session' (port 5432)")
        sys.exit(1)

    # Mask password for display
    masked = DATABASE_URL
    try:
        start = masked.index(":", masked.index("@") - 50) + 1
        end = masked.index("@")
        masked = masked[:start] + "****" + masked[end:]
    except ValueError:
        pass
    print(f"✅ DATABASE_URL terdeteksi: {masked}")


def test_connection():
    """Step 1: Test database connectivity."""
    print_step(1, "Testing koneksi database")

    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            version = result.scalar()
            print(f"✅ Berhasil terhubung ke database!")
            print(f"   PostgreSQL version: {version[:60]}...")
            return True
    except Exception as e:
        print(f"❌ Gagal terhubung ke database: {e}")
        print()
        print("Kemungkinan masalah:")
        print("  - URL salah atau password salah")
        print("  - IP kamu belum di-whitelist (cek Supabase Network Restrictions)")
        print("  - Port salah (coba 5432 atau 6543)")
        sys.exit(1)


def enable_pgvector():
    """Step 2: Enable pgvector extension for RAG."""
    print_step(2, "Mengaktifkan ekstensi pgvector")

    from sqlalchemy import create_engine, text

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()
            print("✅ Ekstensi pgvector berhasil diaktifkan!")
    except Exception as e:
        print(f"⚠️  Gagal mengaktifkan pgvector: {e}")
        print("   (Pastikan pgvector sudah diaktifkan di Supabase Dashboard → Database → Extensions)")
        print("   Lanjut ke step berikutnya...")


def run_migrations():
    """Step 3: Run Alembic migrations to create tables."""
    print_step(3, "Menjalankan migrasi database (membuat tabel)")

    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode == 0:
            print("✅ Migrasi berhasil! Tabel sudah terbuat:")
            print("   - tenants (data bisnis/toko)")
            print("   - users (pemilik & karyawan)")
            print("   - transactions (catatan transaksi)")
        else:
            stderr = result.stderr.strip()
            if "already exists" in stderr.lower() or "already up to date" in stderr.lower():
                print("✅ Tabel sudah ada sebelumnya (up to date).")
            else:
                print(f"⚠️  Alembic output: {stderr}")
                # Try fallback: create tables directly
                print("   Mencoba membuat tabel secara langsung...")
                from database import engine, Base
                import models  # noqa: F401
                Base.metadata.create_all(bind=engine)
                print("✅ Tabel berhasil dibuat via create_all()!")
    except FileNotFoundError:
        print("⚠️  Alembic tidak ditemukan, membuat tabel secara langsung...")
        from database import engine, Base
        import models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        print("✅ Tabel berhasil dibuat via create_all()!")


def seed_data():
    """Step 4: Insert initial tenant and owner user."""
    print_step(4, "Memasukkan data awal (seeding)")

    from database import SessionLocal
    import models

    db = SessionLocal()
    try:
        # Check if tenant already exists
        existing_tenant = db.query(models.Tenant).filter(
            models.Tenant.id == "default-tenant"
        ).first()

        if existing_tenant:
            print("ℹ️  Tenant 'default-tenant' sudah ada, skip seeding.")
        else:
            tenant = models.Tenant(id="default-tenant", name="Toko Uji Coba AGORA")
            db.add(tenant)
            db.commit()
            print("✅ Tenant dibuat: 'Toko Uji Coba AGORA' (ID: default-tenant)")

        # Check if owner user already exists
        existing_user = db.query(models.User).filter(
            models.User.id == "628123456789"
        ).first()

        if existing_user:
            print("ℹ️  User owner '628123456789' sudah ada, skip seeding.")
        else:
            owner = models.User(
                id="628123456789",
                tenant_id="default-tenant",
                name="Owner AGORA",
            )
            db.add(owner)
            db.commit()
            print("✅ User owner dibuat: '628123456789'")

    except Exception as e:
        db.rollback()
        print(f"❌ Gagal seeding: {e}")
    finally:
        db.close()


def print_summary():
    """Final summary."""
    print(f"\n{'='*60}")
    print("  🎉 SETUP DATABASE SELESAI!")
    print(f"{'='*60}")
    print()
    print("Selanjutnya:")
    print("  1. Jalankan server:  uvicorn Engine:app --host 0.0.0.0 --port 8000")
    print("  2. Buka Swagger UI:  http://localhost:8000/docs")
    print("  3. Test endpoint:    POST /extract-receipt (upload gambar struk)")
    print()
    print("Untuk WhatsApp integration:")
    print("  - Isi META_ACCESS_TOKEN, META_PHONE_NUMBER_ID, META_VERIFY_TOKEN di .env")
    print("  - Deploy ke server publik (Ngrok/Railway/Render)")
    print("  - Daftarkan webhook URL di Meta Developer Console")
    print()


if __name__ == "__main__":
    print()
    print("🚀 AGORA AI Accountant — Database Setup")
    print("=" * 60)

    check_env()
    test_connection()
    enable_pgvector()
    run_migrations()
    seed_data()
    print_summary()
