# Deskripsi Projek: WhatsApp AI Accountant

**WhatsApp AI Accountant** adalah sebuah B2B Micro-SaaS yang dirancang khusus untuk Usaha Mikro, Kecil, dan Menengah (UMKM) di Indonesia. Aplikasi ini mengotomatiskan pencatatan keuangan bisnis, baik pemasukan maupun pengeluaran, menggunakan platform yang sangat familiar bagi pelaku UMKM, yaitu **WhatsApp**.

Proyek ini dibuat untuk acara **Vibecode @ Antigravity** dan berfokus pada kemudahan penggunaan, sehingga pemilik usaha maupun karyawan tidak perlu mempelajari aplikasi baru atau antarmuka yang rumit. 

## Mengapa Aplikasi Ini Dibuat? (Problem Statement)
* **Pencatatan Keuangan yang Sulit:** Banyak pelaku UMKM malas atau kesulitan mencatat keuangan karena aplikasi akuntansi tradisional terlalu kompleks.
* **Data Transaksi Tidak Terstruktur:** Pencatatan seringkali masih dilakukan secara manual, baik ditulis di kertas, memfoto struk, atau sekadar diingat-ingat.
* **Literasi Digital yang Beragam:** Membutuhkan solusi yang tidak mengharuskan pengguna mengunduh aplikasi baru. WhatsApp adalah solusi paling tepat dan familiar.

## Siapa Penggunanya?
1. **Pemilik Usaha (Business Owner):** Memiliki akses ke dashboard utama untuk melihat analitik, mendaftarkan nomor karyawan, dan memantau keseluruhan keuangan.
2. **Karyawan/Kasir:** Mengakses sistem melalui WhatsApp untuk mencatat transaksi operasional harian. Mereka tidak bisa melihat total omzet atau laporan keuangan keseluruhan.

## Fitur Utama & Keunggulan AI
Aplikasi ini tidak sekadar chatbot biasa, melainkan didukung oleh teknologi **Agentic AI** dan **Multi-modal LLM**:

1. **Menerima Berbagai Format Input (Multi-modal):** 
   Pengguna dapat mencatat transaksi dengan mengirimkan:
   - **Teks:** (Contoh: "Bayar listrik 500rb")
   - **Pesan Suara (Voice Note):** AI akan mentranskripsinya otomatis (menggunakan Whisper STT).
   - **Foto Struk/Nota:** AI menggunakan **NVIDIA Nemotron-OCR-v2** dan Vision LLM untuk mengekstrak data dari gambar struk atau tulisan tangan secara cerdas.

2. **AI yang Proaktif & Pintar (Agentic Workflow):**
   - Sistem dilengkapi dengan fungsi *Tool Calling* yang memungkinkan AI secara otomatis mengeksekusi penyimpanan ke database berdasarkan obrolan.
   - Jika pengguna mengirim informasi yang tidak lengkap (misal: "Bayar gaji kasir"), AI tidak akan menebak-nebak, melainkan akan **bertanya kembali** (misal: "Berapa nominal gaji yang dibayarkan, Kak?").

3. **Sinkronisasi Web Dashboard:**
   Setiap transaksi yang berhasil dicatat via WhatsApp akan langsung tersinkronisasi ke Dashboard Web secara *real-time*. Data dipisahkan secara aman antara satu bisnis dengan bisnis lainnya (*Multi-tenant architecture*).

## Teknologi yang Digunakan
* **Frontend:** WhatsApp API (untuk input) dan Next.js/React (untuk Web Dashboard)
* **Backend:** Python (FastAPI/LangGraph)
* **Database:** PostgreSQL (Multi-tenant)
* **AI Stack:** LLM Router, Vision Stack (NVIDIA Nemotron + Vision LLM), dan Vector DB (untuk mengingat riwayat kategori).

Projek ini bertujuan untuk memberikan asisten keuangan virtual 24/7 yang cerdas, cepat, dan semudah *chatting* dengan teman sendiri.
