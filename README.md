# AGORA (Automated Goods & Operations Recording Agent) 🛒🤖

AGORA adalah sistem manajemen operasional cerdas yang menggabungkan WhatsApp, OCR, dan AI untuk membantu UMKM menangani pencatatan transaksi dan pengelolaan barang secara lebih efisien. Proyek ini dikembangkan sebagai solusi MVP untuk otomatisasi pemrosesan struk dan ekstraksi data terstruktur.

## ✨ Fitur Utama

- Menerima foto struk melalui WhatsApp
- Mengekstrak data transaksi menggunakan AI/OCR
- Menyimpan hasil pemrosesan untuk kebutuhan pencatatan operasional
- Mendukung workflow lokal dengan arsitektur layanan terpisah

## 🏗️ Arsitektur Sistem

AGORA terdiri dari dua komponen utama:

1. **WhatsApp Gateway (Node.js)**
   - Menangani koneksi ke WhatsApp
   - Menerima pesan dan media dari pengguna
   - Mengirim gambar struk ke layanan AI untuk diproses

2. **AI Engine (Python/FastAPI)**
   - Menyediakan endpoint API untuk pemrosesan gambar/teks
   - Menggunakan model vision/multimodal dari NVIDIA melalui API
   - Menghasilkan payload data transaksi yang terstruktur

## ✅ Prasyarat

Pastikan perangkat Anda memiliki:

- Node.js dan npm
- Python 3.10+
- Docker dan Docker Compose (opsional, tetapi disarankan)
- Aplikasi WhatsApp aktif di ponsel untuk proses scan QR code

## 🚀 Menjalankan Secara Lokal

### 1. Clone repository

```bash
git clone https://github.com/FlintsXzzz/AGORA
cd AGORA
```

### 2. Instal dependensi Node.js

```bash
npm install
```

### 3. Instal dependensi Python

```bash
pip install -r agora_requirements.txt
```

### 4. Siapkan environment variable

Buat file `.env` pada direktori proyek atau sesuaikan environment Anda dengan variabel berikut:

```env
NVIDIA_API_KEY=your_api_key_here
AGORA_STORAGE_DIR=./storage
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agora
```

> Jangan commit file `.env` ke repositori. File ini harus tetap lokal karena berisi kredensial sensitif.

### 5. Jalankan migrasi database (Alembic)

```bash
alembic upgrade head
```

### 6. Jalankan layanan

Buka dua terminal terpisah.

Terminal 1 untuk WhatsApp Gateway:

```bash
node index.js
```

Terminal 2 untuk AI Engine:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Setelah gateway berjalan, scan QR code yang muncul di terminal untuk menghubungkan akun WhatsApp Anda.

## 🐳 Menjalankan dengan Docker

Jika Anda ingin menjalankan aplikasi melalui Docker, gunakan:

```bash
docker compose up --build
```

## 📱 Cara Menggunakan

1. Jalankan bot WhatsApp
2. Kirim pesan `!ping` untuk memastikan bot aktif
3. Kirim foto struk ke bot
4. Bot akan memproses gambar dan mengirimkan hasil ekstraksi dari AI Engine

## 📁 Struktur Proyek

- `index.js` — gateway WhatsApp
- `main.py` — API AI Engine
- `Dockerfile` — image container untuk aplikasi Node.js
- `compose.yaml` — konfigurasi layanan Docker
- `agora_requirements.txt` — dependency Python
- `package.json` — dependency Node.js

## 🔐 Catatan Keamanan

- Jangan pernah mengcommit file `.env` yang berisi token atau kredensial sensitif
- Gunakan praktik keamanan yang baik saat mengoperasikan layanan di lingkungan publik
- Untuk pelaporan kerentanan, lihat file [SECURITY.md](SECURITY.md)

## 🤝 Kontribusi

Kontribusi sangat terbuka. Silakan buat branch baru, lakukan perubahan, lalu ajukan pull request.
