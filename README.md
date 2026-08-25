<div align="center">
  <img src="AGORA_logo.png" alt="AGORA Logo" width="200" />
  <h1>AGORA (Automated Goods & Operations Recording Agent)</h1>
  <p><strong>AI-Powered WhatsApp Accountant for Indonesian MSMEs (UMKM)</strong></p>
  
  [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688.svg)](https://fastapi.tiangolo.com)
  [![Gemini API](https://img.shields.io/badge/Google_Gemini-3.6_Flash-orange.svg)](https://ai.google.dev/)
  [![WhatsApp API](https://img.shields.io/badge/Meta-WhatsApp_Cloud_API-25D366.svg)](https://developers.facebook.com/docs/whatsapp)
  [![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E.svg)](https://supabase.com/)
</div>

---

## 📖 Latar Belakang

Di tengah pesatnya penetrasi digitalisasi nasional, lanskap teknologi global saat ini sedang mengalami transformasi fundamental dari era **Generative AI** menuju era **Agentic AI**. Era baru ini ditandai oleh pergeseran sistem AI dari sekadar penyedia informasi pasif menjadi entitas otonom yang mampu melakukan pemikiran logis (*reasoning*) dan mengambil tindakan nyata (*action-oriented automation*) secara mandiri.

Sayangnya, potensi lompatan teknologi ini belum dirasakan secara inklusif oleh pelaku Usaha Mikro, Kecil, dan Menengah (UMKM), yang merupakan tulang punggung perekonomian Indonesia. **AGORA** hadir untuk memecahkan tiga masalah utama UMKM:

1. **Hambatan Adopsi Teknologi (*High Friction in Tech Adoption*):** Banyak UMKM enggan menggunakan aplikasi pencatatan modern karena UI/UX yang rumit.
2. **Penundaan Administrasi (*Procrastination Overflow*):** Beban operasional fisik membuat pencatatan ditunda hingga malam hari, memicu risiko hilang nota dan kesalahan input manual.
3. **Kebutuhan Solusi Instan (*Instant Solution in Critical Moments*):** Pencatatan harus beroperasi dalam hitungan detik di sela-sela melayani pelanggan tanpa mengganggu operasional.

## 🚀 Solusi AGORA

Melalui pemanfaatan platform komunikasi harian **WhatsApp** sebagai pintu gerbang utama (*conversational gateway*), AGORA menjembatani kesenjangan digital tersebut. 

Pengguna cukup **mengambil dan mengirimkan foto struk belanja** atau sekadar mengirim pesan teks (chat) seperti biasa ke nomor WhatsApp bot AGORA. Di balik layar, *Agentic AI* AGORA bekerja secara otomatis:
- Mengekstrak data menggunakan **Vision OCR**.
- Melakukan pemikiran klasifikasi item dan kategori menggunakan **Function Calling**.
- Menyimpan data transaksi dengan standar akuntansi *double-entry*.
- Secara otomatis dan *real-time* mengupdate stok inventaris di database.

## 🎯 Tujuan & Manfaat

### Tujuan
- **Efisiensi Instan:** Memfasilitasi pencatatan kas masuk/keluar melalui WhatsApp tanpa menyita waktu.
- **Kategorisasi Otomatis:** Mengeliminasi beban kategorisasi manual melalui *reasoning AI*.
- **Integrasi Stok:** Menyatukan log pengeluaran modal langsung ke pembaruan stok (*auto-update*).
- **Fondasi Skalabel:** Membangun arsitektur adaptif berbasis *web application* yang siap diekspansi.

### Manfaat
- **Bagi UMKM:** Tidak perlu belajar UI aplikasi baru, cukup WhatsApp. Mencegah human-error dan menyediakan laporan *real-time*.
- **Bagi Operasional:** Kontrol rantai pasok (*supply chain*) lebih responsif, mencegah *out-of-stock* atau *overstocking*.
- **Bagi Perekonomian:** Membumikan teknologi AI tingkat lanjut (*AI for the Backbone of the Economy*), membuat UMKM lebih terstruktur dan *bankable*.

---

## 🛠️ Arsitektur & Teknologi Utama

- **Backend Framework:** FastAPI (Python)
- **AI & LLM:** Google Gemini 3.6 Flash (Vision OCR, Function Calling)
- **Agentic Framework:** LangGraph / LangChain
- **Database & RAG:** Supabase (PostgreSQL + pgvector)
- **Gateway:** Meta WhatsApp Cloud API & Cloudflare Tunnels

---

## ⚙️ Cara Instalasi & Konfigurasi (Setup)

### 1. Kloning Repository & Instalasi Dependensi
```bash
git clone https://github.com/LDNBillioner/AGORA.git
cd AGORA
pip install -r requirements.txt
```

### 2. Konfigurasi Environment Variables (`.env`)
Buat file `.env` di root direktori proyek, dan isikan kredensial berikut:
```env
# --- Database (Supabase) ---
DATABASE_URL=postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres

# --- Google Gemini ---
GOOGLE_API_KEY=AIzaSyYourGeminiKeyHere

# --- Meta WhatsApp Cloud API ---
META_ACCESS_TOKEN=EAAQ... (Temporary / System User Token dari Meta Dashboard)
META_PHONE_NUMBER_ID=123456789012345
META_VERIFY_TOKEN=agora_verify_token_123

# --- Internal Engine URL ---
AGORA_ENGINE_URL=http://localhost:8000
```

### 3. Setup Database & Migrasi
Jalankan script setup otomatis untuk menguji koneksi, mengaktifkan `pgvector`, melakukan migrasi tabel, dan *seeding* data awal:
```bash
python3 src/setup_db.py
```

### 4. Menjalankan Server Lokal & Cloudflare Tunnel
Buka dua terminal terpisah.
**Terminal 1 (Menjalankan API Server):**
```bash
cd src
uvicorn Engine:app --reload --port 8000
```

**Terminal 2 (Menjalankan Cloudflare Tunnel untuk Webhook):**
```bash
./cloudflared tunnel --url http://localhost:8000
```
*Catatan: Salin URL publik berakhiran `.trycloudflare.com` yang dihasilkan di terminal ini.*

### 5. Konfigurasi Webhook Meta
- Masuk ke **Meta for Developers Dashboard** > WhatsApp > Configuration.
- Edit Webhook, masukkan **Callback URL**: `https://[URL-CLOUDFLARE-ANDA]/webhook`
- Masukkan **Verify Token**: Sesuai dengan `META_VERIFY_TOKEN` di file `.env`.
- Klik Verify and Save, pastikan subscribe ke *messages*.

---

## 📱 Cara Penggunaan Sistem

1. **Mulai Percakapan:** Simpan nomor WhatsApp bisnis yang telah dihubungkan.
2. **Pencatatan Berbasis Teks:**
   Kirimkan pesan natural seperti:
   > *"Saya baru saja beli bahan baku tepung 5kg harga 50rb, dan mentega 2kg harga 30rb."*
   > AGORA akan merespons dengan konfirmasi transaksi dan langsung menyimpannya ke buku besar.
3. **Pencatatan Berbasis Gambar (OCR):**
   - Ambil foto struk belanja atau nota.
   - Kirim foto tersebut ke WhatsApp AGORA.
   - AGORA akan membaca isi nota, mengekstrak rincian barang, menghitung PPN/Diskon, dan mencatat pengeluaran secara akurat.
4. **Cek Laporan (Dashboard):** Anda bisa mengakses dashboard *real-time* yang terhubung ke Supabase untuk melihat rekapitulasi arus kas (*cashflow*) secara visual.

---
<div align="center">
  <i>Dibangun untuk memberdayakan UMKM Indonesia menuju Era Agentic AI. 🚀</i>
</div>
